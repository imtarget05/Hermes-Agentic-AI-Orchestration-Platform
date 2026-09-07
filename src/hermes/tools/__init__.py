"""Tool registry + guarded executor (§5 of spec).

Key question answered here: under what conditions may an agent call
a tool, and what happens on failure?
- permission: each tool declares required permission; each agent
  declares allowed permissions → denied otherwise.
- retryable: only RetryableToolError triggers retry path; FatalToolError
  goes straight to failure. Denylist guards injection.
"""
from __future__ import annotations

import hashlib
import json as _json
import os as _os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

DENY_PATTERN = re.compile(r"(rm\s+-rf\s+/( |$)|:\(\)\s*\{|:;\s*\}|\bshutdown\b|\breboot\b)", re.IGNORECASE)


def _today_iso() -> str:
    return date.today().isoformat()


class RetryableToolError(Exception):
    pass


class FatalToolError(Exception):
    pass


@dataclass
class ToolSpec:
    name: str
    fn: Callable[..., str]
    permission: str = "general"
    retryable: bool = True
    timeout: int = 30
    description: str = ""


REGISTRY: dict[str, ToolSpec] = {}


def register_tool(name: str, permission: str = "general", retryable: bool = True,
                  timeout: int = 30, description: str = ""):
    def deco(fn: Callable[..., str]):
        REGISTRY[name] = ToolSpec(name, fn, permission, retryable, timeout, description)
        return fn
    return deco


def guard_input(text: str) -> None:
    if DENY_PATTERN.search(text or ""):
        raise FatalToolError("Input blocked by injection/denylist guard")


@dataclass
class ToolExecutor:
    allowed_permissions: set[str] = field(default_factory=lambda: {"general"})
    max_retries: int = 3
    log: list[dict] = field(default_factory=list)

    def can_call(self, name: str) -> bool:
        spec = REGISTRY.get(name)
        return bool(spec and spec.permission in self.allowed_permissions)

    def call(self, name: str, **kwargs) -> str:
        spec = REGISTRY.get(name)
        if not spec:
            raise FatalToolError(f"Unknown tool: {name}")
        if spec.permission not in self.allowed_permissions:
            raise FatalToolError(f"Permission denied: agent lacks '{spec.permission}' for tool '{name}'")
        raw = " ".join(str(v) for v in kwargs.values())
        guard_input(raw)
        attempts = 0
        while True:
            try:
                out = spec.fn(**kwargs)
                self.log.append({"tool": name, "ok": True, "attempts": attempts + 1})
                return out
            except FatalToolError:
                self.log.append({"tool": name, "ok": False, "fatal": True})
                raise
            except Exception as e:
                attempts += 1
                if not spec.retryable or attempts > self.max_retries:
                    self.log.append({"tool": name, "ok": False, "attempts": attempts})
                    raise RetryableToolError(f"Tool '{name}' failed after {attempts} attempts: {e}") from e
                time.sleep(min(2 ** attempts * 0.05, 1.0))


# ---- Procurement domain tools (Enterprise Procurement Case Agent) ----


def _approved_vendors_path() -> str:
    here = Path(__file__).resolve()
    # src/hermes/tools/__init__.py → src/hermes/procurement/vendors.json
    candidate = here.parent.parent / "procurement" / "vendors.json"
    if candidate.exists():
        return str(candidate)
    return _os.environ.get("HERMES_VENDORS_PATH", "./vendors.json")


def _extract_valid_until(text: str) -> str | None:
    """Extract valid_until date from quote text. Looks for 'valid until', 'expires', 'valid through' patterns."""
    low = text.lower()
    # Pattern: valid until YYYY-MM-DD or DD/MM/YYYY or similar
    patterns = [
        r"valid\s+until\s*:?\s*(\d{4}-\d{2}-\d{2})",
        r"valid\s+until\s*:?\s*(\d{2}[-/]\d{2}[-/]\d{4})",
        r"expires\s*:?\s*(\d{4}-\d{2}-\d{2})",
        r"expires\s*:?\s*(\d{2}[-/]\d{2}[-/]\d{4})",
        r"valid\s+through\s*:?\s*(\d{4}-\d{2}-\d{2})",
        r"valid\s+through\s*:?\s*(\d{2}[-/]\d{2}[-/]\d{4})",
        r"expiry\s+date\s*:?\s*(\d{4}-\d{2}-\d{2})",
        r"expiry\s+date\s*:?\s*(\d{2}[-/]\d{2}[-/]\d{4})",
    ]
    for pat in patterns:
        m = re.search(pat, low)
        if m:
            d = m.group(1)
            # Normalize to YYYY-MM-DD
            if re.match(r"\d{2}[-/]\d{2}[-/]\d{4}", d):
                parts = re.split(r"[-/]", d)
                return f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
            return d
    return None


def _extract_legal_entity(text: str) -> str | None:
    """Extract legal entity (company name) from quote text."""
    low = text.lower()
    # Common patterns for legal entity
    patterns = [
        r"legal\s+entity\s*:?\s*([^\n]+)",
        r"company\s+name\s*:?\s*([^\n]+)",
        r"issued\s+by\s*:?\s*([^\n]+)",
        r"seller\s*:?\s*([^\n]+)",
        r"vendor\s*:?\s*([^\n]+)",
    ]
    for pat in patterns:
        m = re.search(pat, low)
        if m:
            return m.group(1).strip()
    return None


def _extract_tax_included(text: str) -> bool | None:
    """Extract whether tax is included."""
    low = text.lower()
    if re.search(r"tax\s+included|vat\s+included|including\s+tax|incl\.?\s+tax", low):
        return True
    if re.search(r"tax\s+excluded|vat\s+excluded|excluding\s+tax|excl\.?\s+tax|plus\s+tax|tax\s+extra", low):
        return False
    return None


def _extract_currency(text: str) -> str | None:
    """Extract currency from quote text."""
    # ISO 4217 codes
    currencies = ["USD", "EUR", "GBP", "VND", "JPY", "CNY", "SGD", "AUD", "CAD", "CHF"]
    for curr in currencies:
        if re.search(rf"\b{curr}\b", text, re.IGNORECASE):
            return curr
    # Symbols
    if "$" in text and "USD" not in text.upper():
        return "USD"
    if "€" in text:
        return "EUR"
    if "£" in text:
        return "GBP"
    if "₫" in text or "VND" in text.upper():
        return "VND"
    return None


def _extract_incoterms(text: str) -> str | None:
    """Extract Incoterms from quote text."""
    low = text.lower()
    incoterms_list = [
        "exw", "fca", "fas", "fob", "cfr", "cif", "cpt", "cip",
        "dap", "dpu", "ddp"
    ]
    for term in incoterms_list:
        if re.search(rf"\b{term}\b", low):
            return term.upper()
    return None


def _compute_source_hash(path: str) -> str | None:
    """Compute SHA256 hash of a file for source integrity."""
    try:
        p = Path(path)
        if not p.exists():
            return None
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except Exception:
        return None


def _parse_quote_text(text: str, source_uri: str = "") -> dict:
    t = text or ""
    low = t.lower()
    vendor = ""
    for v, canonical in (("lenovo", "Lenovo"), ("dell", "Dell"), ("hp", "HP")):
        if v in low:
            vendor = canonical
            break
    m = re.search(r"\$\s?([\d,]+(?:\.\d+)?)", t)
    unit = float(m.group(1).replace(",", "")) if m else 0.0
    qty = 0
    for pat in (r"quantity:\s*(\d+)", r"qty:\s*(\d+)",
                r"(\d+)\s+laptops?", r"(\d+)\s+units?"):
        mq = re.search(pat, low)
        if mq:
            qty = int(mq.group(1))
            break
    totals = [float(x.replace(",", "")) for x in re.findall(r"\$\s?([\d,]+(?:\.\d+)?)", t)]
    total = max(totals) if totals else unit * qty
    if qty and unit and not totals:
        total = unit * qty

    # P0-2: Extract new trust fields
    valid_until = _extract_valid_until(t)
    legal_entity = _extract_legal_entity(t)
    tax_included = _extract_tax_included(t)
    currency = _extract_currency(t)
    incoterms = _extract_incoterms(t)
    source_hash = _compute_source_hash(source_uri) if source_uri else None

    return {
        "vendor": vendor,
        "unit_price": unit,
        "quantity": qty,
        "total": total,
        "source_uri": source_uri,
        "raw_text": t[:4000],
        "quote_date": _today_iso(),
        "is_demo": False,
        "valid_until": valid_until,
        "retrieved_at": _today_iso(),
        "legal_entity": legal_entity,
        "tax_included": tax_included if tax_included is not None else True,
        "currency": currency or "USD",
        "incoterms": incoterms,
        "status": "UNKNOWN",  # will be computed by quote_validity_check
        "source_hash": source_hash,
    }


@register_tool("parse_quote_pdf", permission="general", retryable=False,
               description="Parse a vendor quote PDF inside sandbox → Quote JSON")
def parse_quote_pdf(path: str, sandbox: str = "./sandbox") -> str:
    guard_input(path)
    base = Path(sandbox).resolve()
    target = (base / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    if not str(target).startswith(str(base)):
        raise FatalToolError("parse_quote_pdf: path outside sandbox")
    if not target.exists():
        raise FatalToolError(f"parse_quote_pdf: not found: {path}")
    try:
        from pypdf import PdfReader
    except Exception as e:
        raise FatalToolError(f"parse_quote_pdf: pypdf missing (pip install pypdf): {e}")
    try:
        reader = PdfReader(str(target))
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception as e:
        raise FatalToolError(f"parse_quote_pdf: unreadable PDF: {e}")
    if not text.strip():
        raise FatalToolError("parse_quote_pdf: no extractable text (scanned image PDF needs OCR)")
    return _json.dumps(_parse_quote_text(text, source_uri=path))


@register_tool("retrieve_evidence", permission="general",
               description="RAG retrieval over the case index (quotes + vendors + spec) → cited chunks")
def retrieve_evidence(query: str, top_k: int = 3, index_path: str = "") -> str:
    guard_input(query or "")
    path = index_path or _os.environ.get("HERMES_RAG_INDEX", "")
    if not path:
        raise FatalToolError("retrieve_evidence: no index (HERMES_RAG_INDEX unset)")
    from ..rag import RagIndex, format_hits
    index = RagIndex.load(path)
    if not len(index):
        raise FatalToolError(f"retrieve_evidence: empty index at {path}")
    hits = index.query(query, top_k=max(1, min(10, int(top_k or 3))))
    return format_hits(hits)


@register_tool("compare_prices", permission="procurement_price", description="Compare Quote JSON list → ranked totals")
def compare_prices(quotes_json: str) -> str:
    try:
        quotes = _json.loads(quotes_json)
    except Exception as e:
        raise FatalToolError(f"compare_prices: invalid quotes JSON: {e}")
    if not isinstance(quotes, list) or not quotes:
        raise FatalToolError("compare_prices: empty quote list")
    ranked = sorted(quotes, key=lambda q: float(q.get("total", 0) or 0))
    lines = [f"{q.get('vendor','?')}: ${float(q.get('total',0)):,.0f} "
             f"(${float(q.get('unit_price',0)):,.0f} x {q.get('quantity',0)})" for q in ranked]
    return "PRICE RANKING (lowest first):\n" + "\n".join(lines) + f"\nLOWEST: {ranked[0].get('vendor','')}"


@register_tool("check_approved_vendor", permission="procurement_vendor",
               description="Check vendor against approved list → VendorStatus JSON")
def check_approved_vendor(vendor: str, vendors_path: str = "") -> str:
    guard_input(vendor)
    vp = vendors_path or _approved_vendors_path()
    try:
        data = _json.loads(Path(vp).read_text())
        approved_map = {k.lower(): bool(v) for k, v in (data.get("approved_vendors") or {}).items()}
        notes = data.get("notes") or {}
    except Exception:
        approved_map, notes = {"lenovo": True, "dell": True, "hp": False}, {}
    key = (vendor or "").strip().lower()
    approved = approved_map.get(key, False)
    note = notes.get(key, "Approved Vendor ✓" if approved else "Not approved ✗")
    return _json.dumps({"vendor": vendor, "approved": approved, "note": note})


@register_tool("extract_contract_terms", permission="procurement_contract",
               description="Extract payment/warranty/SLA from quote text → ContractTerms JSON")
def extract_contract_terms(quote_text: str, vendor: str = "", source_uri: str = "") -> str:
    t = quote_text or ""
    mp = re.search(r"net\s*(\d+)", t, re.IGNORECASE)
    payment = f"Net {mp.group(1)}" if mp else ""
    mw = re.search(r"(\d+(?:\.\d+)?)\s*years?\s*warranty", t, re.IGNORECASE)
    warranty = float(mw.group(1)) if mw else 0.0
    ms = re.search(r"(\d+(?:\.\d+)?)\s*hours?\b.*?sla|sla.*?(\d+(?:\.\d+)?)\s*hours?", t, re.IGNORECASE)
    sla = 0.0
    if ms:
        sla = float(next(g for g in ms.groups() if g))
    return _json.dumps({"vendor": vendor, "payment": payment, "warranty_years": warranty,
                        "sla_hours": sla, "source_uri": source_uri})


@register_tool("score_spec", permission="procurement_spec",
               description="Score quote spec vs required spec → SpecScore JSON")
def score_spec(quote_text: str, required_spec: str = "", vendor: str = "") -> str:
    q = (quote_text or "").lower()
    r = (required_spec or "").strip().lower()
    # No spec was actually requested → the spec dimension is not applicable.
    # We must NOT invent a failing score by scoring against generic keywords:
    # that would disqualify every vendor for a requirement that was never given.
    if not r or not [w for w in re.findall(r"[a-z0-9]+", r) if len(w) > 2]:
        return _json.dumps({"vendor": vendor, "score": 100.0, "meets_minimum": True,
                            "notes": "no required specification provided — spec N/A"})
    keywords = [w for w in re.findall(r"[a-z0-9]+", r) if len(w) > 2]
    hits = sum(1 for k in keywords if k in q)
    score = round(hits / max(1, len(keywords)) * 100, 1)
    return _json.dumps({"vendor": vendor, "score": score,
                        "meets_minimum": score >= 50.0,
                        "notes": f"{hits}/{len(keywords)} spec keywords matched"})


@register_tool("web_search", permission="research", description="Mockable web search")
def web_search(query: str, mock: str = "") -> str:
    if mock:
        return mock
    return f"[search stub] results for: {query} (plug real API later)"


@register_tool("read_file", permission="general", retryable=False, description="Read file inside sandbox")
def read_file(path: str, sandbox: str = "./sandbox") -> str:
    guard_input(path)
    base = Path(sandbox).resolve()
    target = (base / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    if base not in target.parents and target != base:
        # allow only sandbox subtree
        if not str(target).startswith(str(base)):
            raise FatalToolError("read_file: path outside sandbox")
    if not target.exists():
        raise FatalToolError(f"read_file: not found: {path}")
    return target.read_text()[:8000]


@register_tool("write_file", permission="build", description="Write file inside sandbox")
def write_file(path: str, content: str, sandbox: str = "./sandbox") -> str:
    guard_input(path + content[:2000])
    base = Path(sandbox).resolve()
    base.mkdir(parents=True, exist_ok=True)
    target = (base / path).resolve()
    if not str(target).startswith(str(base)):
        raise FatalToolError("write_file: path outside sandbox")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content[:50000])
    return f"wrote {len(content)} chars to {path}"


@register_tool("run_shell", permission="build", timeout=15, description="Allowlisted shell")
def run_shell(cmd: str) -> str:
    guard_input(cmd)
    allowed = ("echo ", "ls ", "pwd", "python3 --version", "cat ")
    if not any(cmd.strip().startswith(a) for a in allowed):
        raise FatalToolError(f"run_shell: command not allowlisted: {cmd!r}")
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
        return (r.stdout + r.stderr)[:4000] or "(no output)"
    except subprocess.TimeoutExpired as e:
        raise RetryableToolError(f"run_shell timeout: {e}") from e

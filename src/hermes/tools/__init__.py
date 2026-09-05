"""Tool registry + guarded executor (§5 of spec).

Key question answered here: under what conditions may an agent call
a tool, and what happens on failure?
- permission: each tool declares required permission; each agent
  declares allowed permissions → denied otherwise.
- retryable: only RetryableToolError triggers retry path; FatalToolError
  goes straight to failure. Denylist guards injection.
"""
from __future__ import annotations

import json as _json
import os as _os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

DENY_PATTERN = re.compile(r"(rm\s+-rf\s+/( |$)|:\(\)\s*\{|:;\s*\}|\bshutdown\b|\breboot\b)", re.IGNORECASE)


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
    return {"vendor": vendor, "unit_price": unit, "quantity": qty,
            "total": total, "source_uri": source_uri, "raw_text": t[:4000]}


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
    q, r = (quote_text or "").lower(), (required_spec or "").lower()
    keywords = [w for w in re.findall(r"[a-z0-9]+", r) if len(w) > 2]
    if not keywords:
        keywords = ["cpu", "ram", "ssd", "display", "warranty"]
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

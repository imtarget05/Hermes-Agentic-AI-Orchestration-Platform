"""Runtime bootstrap shared by CLI gateway and HTTP API (deployment entrypoint).

Procurement flow: allowlist → route → parse quote PDFs → create task →
orchestrate (DAG) → recommendation (PENDING_APPROVAL → Telegram approval).
"""
from __future__ import annotations

import json
from pathlib import Path

from .agents import configure_agents_llm
from .config import settings
from .llm import build_llm, build_router_classifier
from .messaging import SafeNotifier, build_notifier
from .orchestrator import orchestrate
from .router import RouterAgent, RoutingRegistry
from .tasks import Task, TaskStore
from .tools import ToolExecutor


def parse_quote_files(paths: list[str], sandbox: str = "") -> list[dict]:
    """Parse vendor quote PDFs inside the sandbox → Quote dicts."""
    sandbox = sandbox or settings.hermes_sandbox_dir
    ex = ToolExecutor({"general"})
    quotes = []
    for p in paths or []:
        out = ex.call("parse_quote_pdf", path=p, sandbox=sandbox)
        try:
            quotes.append(json.loads(out))
        except Exception:
            continue
    return quotes


class HermesRuntime:
    """Wires registry + LLM + router + store + notifier once, runs tasks."""

    def __init__(self) -> None:
        self.registry = RoutingRegistry(settings.hermes_routing_path)
        self.llm = build_llm(
            settings.llm_provider,
            settings.cloudflare_model or settings.llm_model,
            settings.cloudflare_account_id,
            settings.cloudflare_api_token,
            settings.cloudflare_timeout,
        )
        configure_agents_llm(self.llm)
        self.router = RouterAgent(self.registry, classify=build_router_classifier(self.llm, self.registry.projects()))
        self.store = TaskStore(settings.hermes_db_path, dsn=settings.hermes_database_url or None)
        self.notifier = SafeNotifier(build_notifier(settings.telegram_bot_token, self.registry))

    @property
    def llm_mode(self) -> str:
        return f"cloudflare {self.settings_model}" if self.llm else "stub"

    @property
    def settings_model(self) -> str:
        return self.settings.cloudflare_model or self.settings.llm_model

    @property
    def settings(self):
        from .config import settings
        return settings

    @property
    def notifier_mode(self) -> str:
        return "telegram" if self.settings.telegram_bot_token else "mock"

    def run_task(self, text: str, project: str = "", strategy: str = "procurement", user: str = "local") -> Task:
        """Full pipeline: allowlist → route → create → orchestrate. Raises on failure."""
        return self.run_procurement(text, project=project, user=user)

    def run_procurement(self, text: str, project: str = "", user: str = "local",
                        quotes: list[dict] | None = None,
                        quote_paths: list[str] | None = None,
                        required_spec: str = "") -> Task:
        """Procurement case: parse PDFs (if given) → DAG → recommendation."""
        if self.settings.allowed_users and user not in self.settings.allowed_users:
            raise PermissionError(f"User '{user}' not in allowlist")
        proj, route = self.router.route(text, project)
        if quote_paths:
            quotes = (quotes or []) + parse_quote_files(quote_paths)
        if not quotes:
            quotes = default_demo_quotes()
        task = self.store.create(Task(text=text, project=proj, strategy="procurement",
                                      max_retries=self.settings.max_retries))
        orchestrate(task.id, self.store, self.notifier, quotes=quotes,
                     required_spec=required_spec)
        return self.store.get(task.id)


def default_demo_quotes() -> list[dict]:
    """Fallback demo quotes (Dell/Lenovo/HP) when no PDF is uploaded."""
    return [
        {"vendor": "Dell", "unit_price": 1200, "quantity": 50, "total": 60000,
         "source_uri": "demo/dell.pdf",
         "raw_text": "Dell quote $1200 x 50 laptops. Payment Net 30, 3 years warranty, SLA 4 hours."},
        {"vendor": "Lenovo", "unit_price": 1080, "quantity": 50, "total": 54000,
         "source_uri": "demo/lenovo.pdf",
         "raw_text": "Lenovo quote $1080 x 50 laptops. Payment Net 30, 3 years warranty, SLA 4 hours."},
        {"vendor": "HP", "unit_price": 1150, "quantity": 50, "total": 57500,
         "source_uri": "demo/hp.pdf",
         "raw_text": "HP quote $1150 x 50 laptops. Payment Net 45, 2 years warranty, SLA 8 hours."},
    ]


def _minimal_text_pdf(lines: list[str]) -> bytes:
    """Build a minimal valid PDF with real extractable text (no extra deps)."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    text_ops = "".join(
        f"BT /F1 12 Tf 72 {800 - i * 20} Td ({esc(text_line)}) Tj ET\n"
        for i, text_line in enumerate(lines)
    )
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(text_ops.encode())} >>\nstream\n{text_ops}endstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF").encode()
    return bytes(out)


def ensure_demo_quote_pdfs(sandbox: str = "") -> list[str]:
    """Materialize the 3 demo quotes as real PDFs in the sandbox (for PDF-path tests)."""
    sandbox = sandbox or settings.hermes_sandbox_dir
    out_paths = []
    for q in default_demo_quotes():
        rel = q["source_uri"]
        target = Path(sandbox).resolve() / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        pdf = _minimal_text_pdf([
            f"QUOTATION - {q['vendor']}",
            f"Unit price: ${q['unit_price']}",
            f"Quantity: {q['quantity']} laptops",
            f"Total: ${q['total']}",
            q["raw_text"],
        ])
        target.write_bytes(pdf)
        out_paths.append(rel)
    return out_paths

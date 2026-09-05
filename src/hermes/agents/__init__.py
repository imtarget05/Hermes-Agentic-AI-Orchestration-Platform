"""Enterprise Procurement Case Agent — specialized agents with isolated permissions.

Replaces the generic research/builder/validator agents:
  Price Agent → compare_prices / parse_quote_pdf
  Vendor Agent → check_approved_vendor
  Contract Agent → extract_contract_terms
  Spec Agent → score_spec
  Analysis Agent → aggregate 4 parallel outputs → Recommendation JSON
  Verification Agent → evidence-grounded check (every claim needs evidence_ref)

Each agent: system prompt + allowed tool permissions + run().
LLM hook injectable; deterministic stub fallback so tests run without API key.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..tools import ToolExecutor


@dataclass
class BaseAgent:
    name: str
    role: str
    system_prompt: str
    allowed_permissions: set[str] = field(default_factory=lambda: {"general"})
    llm: object = None  # callable(prompt)->str | None = stub

    def executor(self, max_retries: int = 3) -> ToolExecutor:
        return ToolExecutor(set(self.allowed_permissions), max_retries)

    def think(self, task_text: str, context: str = "") -> str:
        if self.llm:
            try:
                return str(self.llm(f"{self.system_prompt}\nTask: {task_text}\nContext: {context}"))
            except Exception as e:
                return f"[{self.name} llm-fallback: {e}] {context}"
        return f"[{self.name}:{self.role}] processed: {task_text[:200]} | ctx: {context[:200]}"

    def run(self, task_text: str, context: str = "", tool_calls: list[dict] | None = None,
            max_retries: int = 3) -> str:
        ex = self.executor(max_retries)
        ctx = context
        for tc in tool_calls or []:
            try:
                out = ex.call(tc["tool"], **tc.get("args", {}))
                ctx += f"\n[tool:{tc['tool']}] {out[:2000]}"
            except Exception as e:
                ctx += f"\n[tool:{tc['tool']} ERROR] {e}"
        return self.think(task_text, ctx)


# ---- 4 parallel specialists ----

PRICE = BaseAgent(
    name="price", role="Price",
    system_prompt=(
        "You are Price agent. Compare vendor quotes by total cost "
        "(unit_price x quantity). Use compare_prices / parse_quote_pdf only. "
        "Output a price ranking with the lowest vendor first."
    ),
    allowed_permissions={"general", "procurement_price"},
)
VENDOR = BaseAgent(
    name="vendor", role="Vendor",
    system_prompt=(
        "You are Vendor agent. Check each vendor against the approved-vendor "
        "list via check_approved_vendor only. Output approved/not-approved per vendor."
    ),
    allowed_permissions={"general", "procurement_vendor"},
)
CONTRACT = BaseAgent(
    name="contract", role="Contract",
    system_prompt=(
        "You are Contract agent. Extract payment terms, warranty years and SLA "
        "hours via extract_contract_terms only. Output per-vendor terms."
    ),
    allowed_permissions={"general", "procurement_contract"},
)
SPEC = BaseAgent(
    name="spec", role="Specification",
    system_prompt=(
        "You are Specification agent. Score each quote against the required spec "
        "via score_spec only. Output per-vendor score and meets_minimum."
    ),
    allowed_permissions={"general", "procurement_spec"},
)


class AnalysisAgent(BaseAgent):
    """Join node: lowest total + approved + warranty/SLA → Recommendation JSON."""

    def think(self, task_text: str, context: str = "") -> str:
        if self.llm:
            return super().think(task_text, context)
        return self._deterministic(task_text, context)

    def _deterministic(self, task_text: str, context: str) -> str:
        quotes = self._quotes_from_ctx(context)
        approved = self._approved_from_ctx(context)
        terms = self._terms_from_ctx(context)
        # eligible = approved vendors only; fallback to all if none marked
        eligible = [q for q in quotes if approved.get(q.get("vendor", ""), True)]
        pool = eligible or quotes
        if not pool:
            return json.dumps({"vendor": "", "total_cost": 0.0, "reasons": [],
                               "evidence_refs": [], "status": "DRAFT"})
        best = min(pool, key=lambda q: float(q.get("total", 0) or 0))
        vendor = best.get("vendor", "")
        total = float(best.get("total", 0) or 0)
        t = terms.get(vendor, {})
        reasons = [
            {"claim": f"lowest total cost ${total:,.0f}", "evidence_ref": best.get("source_uri", "quotes")},
            {"claim": "approved vendor", "evidence_ref": f"vendors.json:{vendor}"},
        ]
        if t.get("warranty_years"):
            reasons.append({"claim": f"{t['warranty_years']:g}-year warranty",
                            "evidence_ref": t.get("source_uri", "quotes") or "quotes"})
        if t.get("payment"):
            reasons.append({"claim": f"payment {t['payment']}", "evidence_ref": t.get("source_uri", "quotes") or "quotes"})
        if t.get("sla_hours"):
            reasons.append({"claim": f"SLA {t['sla_hours']:g} hours",
                            "evidence_ref": t.get("source_uri", "quotes") or "quotes"})
        rec = {"vendor": vendor, "total_cost": total, "reasons": reasons,
               "evidence_refs": [r["evidence_ref"] for r in reasons], "status": "PENDING_APPROVAL"}
        return json.dumps(rec)

    @staticmethod
    def _quotes_from_ctx(ctx: str) -> list[dict]:
        quotes: list[dict] = []
        for line in ctx.splitlines():
            s = line.strip()
            if s.startswith("[") and "vendor" in s and "total" in s:
                try:
                    data = json.loads(s)
                    if isinstance(data, list):
                        quotes.extend(data)
                    elif isinstance(data, dict):
                        quotes.append(data)
                except Exception:
                    continue
        return quotes

    @staticmethod
    def _approved_from_ctx(ctx: str) -> dict[str, bool]:
        out: dict[str, bool] = {}
        for line in ctx.splitlines():
            s = line.strip()
            if '"approved"' in s and '"vendor"' in s:
                try:
                    start = s.find("{")
                    data = json.loads(s[start:])
                    if isinstance(data, dict) and data.get("vendor"):
                        out[str(data["vendor"])] = bool(data.get("approved"))
                except Exception:
                    continue
        # also handle bare lowercase keys from tools
        return out

    @staticmethod
    def _terms_from_ctx(ctx: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for line in ctx.splitlines():
            s = line.strip()
            if '"warranty_years"' in s:
                try:
                    start = s.find("{")
                    data = json.loads(s[start:])
                    if isinstance(data, dict) and data.get("vendor"):
                        out[str(data["vendor"])] = data
                except Exception:
                    continue
        return out


class VerificationAgent(BaseAgent):
    """Check the recommendation is grounded in evidence (fail → retry)."""

    def think(self, task_text: str, context: str = "") -> str:
        if self.llm:
            return super().think(task_text, context)
        from ..procurement.schemas import Recommendation
        rec = Recommendation.from_text(self._extract_json(context) or context)
        problems: list[str] = []
        if not rec.vendor:
            problems.append("no vendor selected")
        if not rec.reasons:
            problems.append("no reasons given")
        for r in rec.reasons:
            if not r.evidence_ref:
                problems.append(f"claim without evidence: {r.claim[:80]}")
        if not rec.evidence_refs:
            problems.append("no evidence_refs")
        if problems:
            return "VERIFICATION FAILED: " + "; ".join(problems)
        return "VERIFICATION PASSED\n" + rec.to_text()

    @staticmethod
    def _extract_json(ctx: str) -> str:
        start = ctx.find('{"vendor"')
        if start == -1:
            start = ctx.find("{")
        if start == -1:
            return ""
        depth, end = 0, -1
        for i in range(start, len(ctx)):
            if ctx[i] == "{":
                depth += 1
            elif ctx[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        return ctx[start:end] if end > 0 else ""


ANALYSIS = AnalysisAgent(
    name="analysis", role="Analysis",
    system_prompt=(
        "You are Analysis agent. Aggregate price/vendor/contract/spec outputs. "
        "Recommend the lowest-cost APPROVED vendor with acceptable warranty/SLA. "
        "Reply ONLY with Recommendation JSON: "
        '{"vendor, total_cost, reasons:[{claim, evidence_ref}], evidence_refs, status}.'
    ),
    allowed_permissions={"general"},
)
VERIFICATION = VerificationAgent(
    name="verification", role="Verification",
    system_prompt=(
        "You are Verification agent. Check the recommendation is grounded in "
        "evidence: every reason must cite an evidence_ref (quote URI / vendors.json). "
        "Reply VERIFICATION PASSED + summary, or VERIFICATION FAILED + problems."
    ),
    allowed_permissions={"general"},
)

AGENTS: dict[str, BaseAgent] = {
    "price": PRICE,
    "vendor": VENDOR,
    "contract": CONTRACT,
    "spec": SPEC,
    "analysis": ANALYSIS,
    "verification": VERIFICATION,
}


def configure_agents_llm(llm) -> None:
    """Inject shared LLM callable into all agents (None = stub mode)."""
    for a in AGENTS.values():
        a.llm = llm

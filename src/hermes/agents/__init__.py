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

# Grounded-price policy (incident: invented market prices).
# Every agent reporting figures must use ONLY tool/RAG-provided numbers and
# must never invent prices, specs or model details from memory.
_GROUNDING = (
    " Grounding rule: use ONLY figures from tool outputs / retrieved evidence; "
    "never invent prices, specs or model details. Every figure must be traceable "
    "to a quote evidence_ref, and quotes carry quote_date (or DEMO for samples)."
)


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
        "Output a price ranking with the lowest vendor first." + _GROUNDING
    ),
    allowed_permissions={"general", "procurement_price"},
)
VENDOR = BaseAgent(
    name="vendor", role="Vendor",
    system_prompt=(
        "You are Vendor agent. Check each vendor against the approved-vendor "
        "list via check_approved_vendor only. Output approved/not-approved per vendor."
        + _GROUNDING
    ),
    allowed_permissions={"general", "procurement_vendor"},
)
CONTRACT = BaseAgent(
    name="contract", role="Contract",
    system_prompt=(
        "You are Contract agent. Extract payment terms, warranty years and SLA "
        "hours via extract_contract_terms only. Output per-vendor terms." + _GROUNDING
    ),
    allowed_permissions={"general", "procurement_contract"},
)
SPEC = BaseAgent(
    name="spec", role="Specification",
    system_prompt=(
        "You are Specification agent. Score each quote against the required spec "
        "via score_spec only. Output per-vendor score and meets_minimum." + _GROUNDING
    ),
    allowed_permissions={"general", "procurement_spec"},
)


def _looks_like_price(claim: str) -> bool:
    """True if a claim states a monetary figure ($, VND, ₫, total cost)."""
    import re as _re
    t = claim or ""
    return bool(_re.search(r"\$|₫|vnd|total cost|giá", t, _re.IGNORECASE))


def _quotes_as_of(quotes: list[dict]) -> str:
    """Freshness label for a recommendation: DEMO | ISO date | date range | ''."""
    if not quotes:
        return ""
    if all(q.get("is_demo") or q.get("quote_date") == "DEMO" for q in quotes):
        return "DEMO"
    dates = sorted({str(q.get("quote_date", "")).strip() for q in quotes
                    if str(q.get("quote_date", "")).strip() and q.get("quote_date") != "DEMO"})
    if not dates:
        return ""
    return dates[0] if len(dates) == 1 else f"{dates[0]}..{dates[-1]}"


class AnalysisAgent(BaseAgent):
    """Join node: deterministic policy engine → Recommendation JSON."""

    def think(self, task_text: str, context: str = "") -> str:
        if self.llm:
            return super().think(task_text, context)
        return self._deterministic(task_text, context)

    def _deterministic(self, task_text: str, context: str) -> str:
        # Lazy import to avoid circular dependency
        from ..decision import PolicyEngine
        from ..procurement.schemas import (
            ContractTerms,
            Quote,
            SpecScore,
            VendorStatus,
        )

        policy_engine = PolicyEngine.load_policy("src/hermes/config/policy.yaml")

        quotes = self._quotes_from_ctx(context)
        approved = self._approved_from_ctx(context)
        terms = self._terms_from_ctx(context)
        spec_scores = self._spec_scores_from_ctx(context)

        # Derive contract terms from the quote evidence when the contract agent
        # did not supply them. The quotes' raw_text IS part of the evidence —
        # letting a missing structured field fall back to warranty=0 / payment=""
        # would disqualify every vendor for terms that are actually present in
        # the source document (information-preservation, not policy weakening).
        if quotes:
            ex = self.executor()
            for q in quotes:
                vendor = str(q.get("vendor", ""))
                raw = str(q.get("raw_text", "") or "")
                if not vendor or vendor in terms or not raw.strip():
                    continue
                try:
                    terms[vendor] = json.loads(ex.call(
                        "extract_contract_terms", quote_text=raw, vendor=vendor,
                        source_uri=str(q.get("source_uri", ""))))
                except Exception:
                    pass

        # Build domain objects
        quote_objs = [Quote(**q) for q in quotes]
        vendor_statuses = {
            v: VendorStatus(vendor=v, approved=approved.get(v, True))
            for v in approved
        }
        contract_terms = {v: ContractTerms(**t) for v, t in terms.items()}
        spec_score_objs = {v: SpecScore(**s) for v, s in spec_scores.items()}

        # Evaluate via policy engine
        rec = policy_engine.evaluate(
            quote_objs, vendor_statuses, contract_terms, spec_score_objs
        )

        return rec.model_dump_json()

    @staticmethod
    def _json_objects(ctx: str, max_window: int = 6000) -> list[dict]:
        out: list[dict] = []
        i, n = 0, len(ctx)
        while i < n:
            start = ctx.find("{", i)
            if start == -1:
                break
            depth, end = 0, -1
            for j in range(start, min(n, start + max_window)):
                if ctx[j] == "{":
                    depth += 1
                elif ctx[j] == "}":
                    depth -= 1
                    if depth == 0:
                        end = j + 1
                        break
            if end == -1:
                i = start + 1
                continue
            try:
                data = json.loads(ctx[start:end])
                if isinstance(data, dict):
                    out.append(data)
            except Exception:
                pass
            i = end
        return out

    @staticmethod
    def _quotes_from_ctx(ctx: str) -> list[dict]:
        return [o for o in AnalysisAgent._json_objects(ctx)
                if o.get("vendor") and ("total" in o) and ("unit_price" in o)]

    @staticmethod
    def _approved_from_ctx(ctx: str) -> dict[str, bool]:
        out: dict[str, bool] = {}
        for o in AnalysisAgent._json_objects(ctx):
            if o.get("vendor") is not None and "approved" in o:
                out[str(o["vendor"])] = bool(o["approved"])
        return out

    @staticmethod
    def _terms_from_ctx(ctx: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for o in AnalysisAgent._json_objects(ctx):
            if o.get("vendor") and "warranty_years" in o:
                out[str(o["vendor"])] = o
        return out

    @staticmethod
    def _spec_scores_from_ctx(ctx: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for o in AnalysisAgent._json_objects(ctx):
            if o.get("vendor") and "score" in o and "meets_minimum" in o:
                out[str(o["vendor"])] = o
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
            elif _looks_like_price(r.claim) and r.evidence_ref.strip().lower() in ("quotes", ""):
                problems.append(f"price claim without dated quote evidence: {r.claim[:80]}")
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
        '{"vendor, total_cost, reasons:[{claim, evidence_ref}], evidence_refs, status, data_as_of}.'
        " Set data_as_of from the quotes' quote_date (or DEMO for samples)."
        + _GROUNDING
    ),
    # procurement_contract: the join node may derive contract terms from the
    # quotes' raw_text evidence when the contract agent supplied none.
    allowed_permissions={"general", "procurement_contract"},
)
VERIFICATION = VerificationAgent(
    name="verification", role="Verification",
    system_prompt=(
        "You are Verification agent. Check the recommendation is grounded in "
        "evidence: every reason must cite an evidence_ref (quote URI / vendors.json). "
        "Price figures must cite a dated quote (quote_date, never DEMO-as-market). "
        "Reply VERIFICATION PASSED + summary, or VERIFICATION FAILED + problems."
        + _GROUNDING
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

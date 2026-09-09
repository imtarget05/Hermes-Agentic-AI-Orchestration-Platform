"""AdvisoryCouncil — many advisor personas evaluate one question.

Runs each persona as an independent opinion-gatherer (parallel node), then
synthesizes. Grounded-price policy applies: no fabricated figures. When an LLM
is available it enriches the framing; otherwise the deterministic stub returns
sober, framework-level guidance with `grounded=False` (no domain data invented).
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from .personas import get_personas
from .schemas import AdvisorOpinion, AdvisorPersona, CouncilReport

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


class AdvisoryCouncil:
    def __init__(self, llm=None, max_workers: int = 4):
        self.llm = llm
        self.max_workers = max_workers

    def ask(self, question: str, personas: list[str] | None = None,
            context: str = "") -> CouncilReport:
        selected = get_personas(personas)
        if not selected:
            selected = get_personas(None)
        results = _run_parallel(
            lambda p: self._opinion(p, question, context), selected,
            self.max_workers)
        synthesized = self._synthesize(results, question)
        return CouncilReport(question=question, opinions=results,
                             synthesized=synthesized)

    def _opinion(self, persona: AdvisorPersona, question: str,
                 context: str) -> AdvisorOpinion:
        if self.llm is not None:
            return self._llm_opinion(persona, question, context)
        return self._stub_opinion(persona, question, context)

    def _stub_opinion(self, persona: AdvisorPersona, question: str,
                      context: str) -> AdvisorOpinion:
        qt = _tokens(question)
        matched = [f for f in persona.focus_areas if _focal_match(qt, f)]
        focus = ", ".join(matched) if matched else "lĩnh vực trọng tâm"
        has_data = bool(context.strip())
        verdict = (
            f"Đối với vấn đề này, khía cạnh {focus} cần được rà soát trước tiên."
            if matched else
            f"Xét theo góc nhìn {persona.domain}, cần làm rõ phạm vi trước khi khuyến nghị."
        )
        reasons = [
            f"Áp dụng framework: {', '.join(persona.framework_refs) or 'n/a'}",
            "Thu thập dữ kiện có nguồn trước khi quyết định (chính sách grounded).",
        ]
        confidence = "high" if matched else "medium"
        return AdvisorOpinion(
            persona=persona.name,
            question=question,
            verdict=verdict,
            reasons=reasons,
            confidence=confidence,
            references=list(persona.framework_refs),
            grounded=has_data,
        )

    def _llm_opinion(self, persona: AdvisorPersona, question: str,
                     context: str) -> AdvisorOpinion:
        try:
            prompt = (
                f"{persona.system_prompt}\n"
                f"Question: {question}\n"
                f"Internal context: {context or '(none)'}\n"
                "Answer with: VERDICT:<one line> REASON:<one bullet per line>\n"
            )
            out = (self.llm.complete(prompt) or "").strip()
            verdict_parts = [l for l in out.splitlines() if l.lower().startswith("verdict")]
            reason_lines = [l for l in out.splitlines() if l.lower().startswith("reason")]
            verdict = verdict_parts[0].split(":", 1)[1].strip() if verdict_parts else out[:120]
            reasons = [l.split(":", 1)[1].strip() for l in reason_lines]
            return AdvisorOpinion(
                persona=persona.name, question=question,
                verdict=verdict, reasons=reasons or ["(LLM trả về không đủ chi tiết)"],
                confidence="high", references=list(persona.framework_refs),
                grounded=bool(context.strip()))
        except Exception:  # noqa: BLE001
            return self._stub_opinion(persona, question, context)

    def _synthesize(self, opinions: list[AdvisorOpinion], question: str) -> str:
        if not opinions:
            return "Hội đồng không có ý kiến."
        names = [o.persona for o in opinions]
        return (
            f"{len(opinions)} persona ({', '.join(names)}) đã đánh giá câu hỏi "
            f'"{question}". Tổng hợp: xem các verdict ở trên; áp dụng chính sách '
            "grounded cho mọi con số trước khi ra quyết định."
        )


def _focal_match(qt: set[str], focus: str) -> bool:
    for token in _tokens(focus):
        if token and token in qt:
            return True
    return False


def _run_parallel(fn, items, workers: int):
    """Run fn over items (parallel if len>1 and workers>1), keep original order."""
    items = list(items)
    if len(items) <= 1 or workers <= 1:
        return [fn(i) for i in items]
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        return list(pool.map(fn, items))


def ask_council(question: str, personas: list[str] | None = None,
                context: str = "", llm=None, max_workers: int = 4) -> CouncilReport:
    return AdvisoryCouncil(llm=llm, max_workers=max_workers).ask(
        question, personas, context)
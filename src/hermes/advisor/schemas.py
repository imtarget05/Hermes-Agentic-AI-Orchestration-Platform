"""Advisory Council schemas — persona config + opinions (①)."""
from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class AdvisorPersona(BaseModel):
    name: str
    domain: str = "general"
    focus_areas: list[str] = Field(default_factory=list)
    system_prompt: str = ""
    framework_refs: list[str] = Field(default_factory=list)
    enabled: bool = True


class AdvisorOpinion(BaseModel):
    persona: str = ""
    question: str = ""
    verdict: str = ""          # short conclusion
    reasons: list[str] = Field(default_factory=list)
    confidence: str = ""       # high | medium | low
    references: list[str] = Field(default_factory=list)
    grounded: bool = True       # False => no data match; only general framing
    produced_at: str = Field(default_factory=_now_iso)

    def to_text(self) -> str:
        lines = [f"🎯 {self.persona}"]
        lines.append(f"   Verdict: {self.verdict}")
        lines.append(f"   Confidence: {self.confidence}")
        if self.reasons:
            lines.append("   Reasons:")
            for r in self.reasons:
                lines.append(f"      - {r}")
        if self.references:
            lines.append("   References:")
            for ref in self.references:
                lines.append(f"      {ref}")
        if not self.grounded:
            lines.append("   ⚠️ Không có dữ liệu nội bộ; chỉ là khung tư vấn chung.")
        return "\n".join(lines)


class CouncilReport(BaseModel):
    question: str = ""
    opinions: list[AdvisorOpinion] = Field(default_factory=list)
    synthesized: str = ""      # short summary across personas
    generated_at: str = Field(default_factory=_now_iso)

    def to_text(self) -> str:
        parts = [f"# Advisory Council — {self.question}", ""]
        parts += [op.to_text() for op in self.opinions]
        if self.synthesized:
            parts += ["", "## Tổng hợp", self.synthesized]
        return "\n".join(parts)
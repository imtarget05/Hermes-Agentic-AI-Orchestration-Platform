"""Competitive Intelligence schemas (③) — targets, findings, weekly brief."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

FindingKind = Literal["post", "pricing", "launch", "news"]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class CompetitorTarget(BaseModel):
    competitor: str = ""
    urls: list[str] = Field(default_factory=list)
    feeds: list[str] = Field(default_factory=list)
    enabled: bool = True


class CompetitorFinding(BaseModel):
    competitor: str = ""
    kind: FindingKind = "post"
    headline: str = ""
    summary: str = ""
    source_uri: str = ""
    observed: str = Field(default_factory=_now_iso)
    raw: str = ""

    def to_text(self) -> str:
        return (
            f"[{self.kind}] {self.competitor}: {self.headline or self.summary} "
            f"[source={self.source_uri}]"
        )


class WeeklyBrief(BaseModel):
    period: str = ""
    generated_at: str = Field(default_factory=_now_iso)
    findings: list[CompetitorFinding] = Field(default_factory=list)
    per_competitor: dict[str, int] = Field(default_factory=dict)
    shifts: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)

    def to_text(self) -> str:
        lines = [f"# 🧭 Competitive brief — {self.period}", ""]
        if self.shifts:
            lines.append("## 📊 Chuyển biến")
            lines += [f"- {s}" for s in self.shifts]
        if self.opportunities:
            lines.append("## 🚀 Cơ hội")
            lines += [f"- {o}" for o in self.opportunities]
        if self.risks:
            lines.append("## ⚠️ Rủi ro")
            lines += [f"- {r}" for r in self.risks]
        if self.findings:
            lines += ["", "## Phát hiện (có nguồn)"]
            lines += [f"- {f.to_text()}" for f in self.findings]
        return "\n".join(lines)
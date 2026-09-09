"""Competitor analyzer + weekly brief builder (③).

`build_weekly_brief` is fully deterministic: it only aggregates findings that
were actually collected (each with a `source_uri`), so it never invents pricing
or launches. Mirrors the repo's grounded-price policy.
"""
from __future__ import annotations

from collections import Counter

from .schemas import CompetitorFinding, WeeklyBrief


def analyze_findings(findings: list[CompetitorFinding]) -> dict[str, Counter]:
    """Group findings by competitor → Counter(kind)."""
    per_competitor: dict[str, Counter] = {}
    for f in findings:
        per_competitor.setdefault(f.competitor, Counter())[f.kind] += 1
    return per_competitor


def build_weekly_brief(findings: list[CompetitorFinding], *,
                       period: str = "") -> WeeklyBrief:
    if not period:
        period = _default_period(findings)
    shifts: list[str] = []
    opportunities: list[str] = []
    risks: list[str] = []
    launches = [f for f in findings if f.kind == "launch"]
    pricing = [f for f in findings if f.kind == "pricing"]

    for f in launches:
        shifts.append(f"Công ty {f.competitor} ra mắt: "
                      f"{f.headline or f.summary} [source={f.source_uri}]")
    for f in pricing:
        shifts.append(f"Công ty {f.competitor} đăng tín hiệu giá: "
                      f"{f.summary} [source={f.source_uri}]")
    if launches:
        opportunities.append("Theo dõi phản hồi thị trường về các sản phẩm mới.")
    if pricing:
        opportunities.append("Rà soát lại định giá của mình trước biến động giá đối thủ.")

    per = analyze_findings(findings)
    for comp, counter in per.items():
        if counter.get("pricing", 0) >= 1 and counter.get("launch", 0) >= 1:
            risks.append(f"Đối thủ {comp} đang đẩy mạnh sản phẩm + định giá; cần theo sát.")

    per_comp = {name: sum(counter.values()) for name, counter in per.items()}
    return WeeklyBrief(
        period=period, findings=list(findings), per_competitor=per_comp,
        shifts=shifts[:10], opportunities=opportunities[:5], risks=risks[:5])


def _default_period(findings: list[CompetitorFinding]) -> str:
    if not findings:
        return "unknown"
    dates = sorted(f.observed[:10] for f in findings if f.observed)
    if len(dates) >= 2:
        return f"{dates[0]}..{dates[-1]}"
    return dates[0] if dates else "unknown"
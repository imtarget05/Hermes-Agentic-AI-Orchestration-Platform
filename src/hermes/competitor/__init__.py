"""Competitive Intelligence package (③) — collect → analyze → weekly brief."""
from .analyze import build_weekly_brief
from .collector import CompetitorCollector
from .schemas import (
    CompetitorFinding,
    CompetitorTarget,
    FindingKind,
    WeeklyBrief,
)

__all__ = [
    "CompetitorCollector",
    "CompetitorFinding",
    "CompetitorTarget",
    "FindingKind",
    "WeeklyBrief",
    "build_weekly_brief",
]
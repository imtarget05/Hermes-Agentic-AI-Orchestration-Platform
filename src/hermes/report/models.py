"""Normalized report data model — shared across all report types."""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ReportType(str, Enum):
    PROCUREMENT = "procurement"
    FINANCIAL = "financial"
    MAINTENANCE = "maintenance"
    RESEARCH = "research"
    INVESTIGATION = "investigation"
    WORKFLOW = "workflow"


class ReportTier(str, Enum):
    """Information density levels — executive first, audit last."""
    EXECUTIVE = "executive"
    EVIDENCE = "evidence"
    AUDIT = "audit"


class EvidenceItem(BaseModel):
    """Single evidence reference with provenance."""
    id: str = ""                    # E-01, E-02, ...
    claim: str = ""                 # What this evidence supports
    source: str = ""                # lenovo_quote.pdf
    source_uri: str = ""            # /sandbox/telegram_123/lenovo_quote.pdf
    agent: str = ""                 # Price Agent
    verification: str = ""          # VERIFIED | PENDING | FAILED
    confidence: float = 0.0         # 0.0 - 1.0
    timestamp: str = ""             # When evidence was collected


class ReportSection(BaseModel):
    """Single section in the report."""
    title: str = ""
    tier: ReportTier = ReportTier.EXECUTIVE
    content: str = ""               # Markdown or structured text
    table_data: list[list[str]] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    visible: bool = True            # Template can hide sections
    order: int = 0                  # Sort order within tier


class DecisionCard(BaseModel):
    """Standard decision summary — appears at end of every report."""
    recommendation: str = ""
    decision: str = ""              # APPROVED | PENDING | REJECTED | UNKNOWN
    confidence: float = 0.0         # 0.0 - 1.0
    policy_status: str = ""         # COMPLIANT | NON_COMPLIANT | UNKNOWN
    evidence_count: int = 0
    verified_count: int = 0
    risk_count: int = 0
    approver: str = ""
    approved_at: str = ""
    execution_status: str = ""      # COMPLETED | PENDING | UNKNOWN


class NormalizedReport(BaseModel):
    """Unified report model — all report types produce this."""
    report_type: ReportType
    title: str = ""
    subtitle: str = ""
    generated_at: str = Field(default_factory=lambda: date.today().isoformat())
    workflow_id: str = ""
    task_id: str = ""

    # Tier 1 — Executive
    executive_summary: str = ""
    key_numbers: dict[str, Any] = Field(default_factory=dict)

    # Tier 2 — Evidence
    evidence_items: list[EvidenceItem] = Field(default_factory=list)

    # Tier 3 — Audit
    audit_trail: list[dict[str, Any]] = Field(default_factory=list)

    # Sections (template-specific)
    sections: list[ReportSection] = Field(default_factory=list)

    # Decision Card (always present)
    decision_card: DecisionCard = Field(default_factory=DecisionCard)

    def sections_by_tier(self, tier: ReportTier) -> list[ReportSection]:
        """Return visible sections for a given tier, sorted by order."""
        return sorted(
            [s for s in self.sections if s.tier == tier and s.visible],
            key=lambda s: s.order,
        )

    def evidence_by_id(self, evidence_id: str) -> EvidenceItem | None:
        """Lookup evidence item by ID."""
        for e in self.evidence_items:
            if e.id == evidence_id:
                return e
        return None

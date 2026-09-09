"""Abstract base template — all report templates inherit from this."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from ..models import (
    DecisionCard,
    EvidenceItem,
    NormalizedReport,
    ReportSection,
    ReportTier,
    ReportType,
)


class BaseTemplate(ABC):
    """Base class for all report templates.

    Subclasses must implement:
    - report_type(): Return the ReportType enum value
    - required_sections(): Return list of required section titles
    - build_sections(): Build sections from workflow data
    """

    @abstractmethod
    def report_type(self) -> ReportType:
        """Return the ReportType enum value."""

    @abstractmethod
    def required_sections(self) -> list[str]:
        """Return list of required section titles."""

    @abstractmethod
    def build_sections(self, data: dict) -> list[ReportSection]:
        """Build report sections from workflow data."""

    def build_report(
        self,
        data: dict,
        workflow_id: str = "",
        task_id: str = "",
    ) -> NormalizedReport:
        """Build complete report from workflow data."""
        sections = self.build_sections(data)
        evidence = self._extract_evidence(data)
        audit_trail = self._extract_audit_trail(data)

        return NormalizedReport(
            report_type=self.report_type(),
            title=self._build_title(data),
            subtitle=self._build_subtitle(data),
            generated_at=date.today().isoformat(),
            workflow_id=workflow_id,
            task_id=task_id,
            executive_summary=self.build_executive_summary(data),
            key_numbers=self._extract_key_numbers(data),
            evidence_items=evidence,
            audit_trail=audit_trail,
            sections=sections,
            decision_card=self._build_decision_card(data, evidence),
        )

    def build_executive_summary(self, data: dict) -> str:
        """Build executive summary — override for custom."""
        return data.get("executive_summary", "")

    def _build_title(self, data: dict) -> str:
        """Build report title — override for custom."""
        return data.get("title", f"HERMES — {self.report_type().value.upper()} REPORT")

    def _build_subtitle(self, data: dict) -> str:
        """Build report subtitle — override for custom."""
        return data.get("subtitle", "")

    def _extract_evidence(self, data: dict) -> list[EvidenceItem]:
        """Extract evidence items from data — override for custom."""
        raw = data.get("evidence", [])
        items = []
        for i, e in enumerate(raw, start=1):
            if isinstance(e, dict):
                items.append(EvidenceItem(
                    id=f"E-{i:02d}",
                    claim=e.get("claim", ""),
                    source=e.get("source", ""),
                    source_uri=e.get("source_uri", ""),
                    agent=e.get("agent", ""),
                    verification=e.get("verification", "PENDING"),
                    confidence=e.get("confidence", 0.0),
                    timestamp=e.get("timestamp", ""),
                ))
            elif isinstance(e, EvidenceItem):
                e.id = e.id or f"E-{i:02d}"
                items.append(e)
        return items

    def _extract_key_numbers(self, data: dict) -> dict:
        """Extract key numbers — override for custom."""
        return data.get("key_numbers", {})

    def _extract_audit_trail(self, data: dict) -> list[dict]:
        """Extract audit trail — override for custom."""
        return data.get("audit_trail", [])

    def _build_decision_card(self, data: dict, evidence: list[EvidenceItem]) -> DecisionCard:
        """Build decision card — override for custom."""
        verified_count = sum(1 for e in evidence if e.verification == "VERIFIED")

        # Handle recommendation - may be dict or string
        recommendation = data.get("recommendation", "")
        if isinstance(recommendation, dict):
            recommendation = recommendation.get("vendor", str(recommendation))

        # Handle confidence - may be string or float
        confidence = data.get("confidence", 0.0)
        if isinstance(confidence, str):
            confidence_map = {"HIGH": 0.9, "MEDIUM": 0.7, "LOW": 0.5}
            confidence = confidence_map.get(confidence.upper(), 0.0)

        return DecisionCard(
            recommendation=str(recommendation),
            decision=data.get("decision", "PENDING"),
            confidence=float(confidence),
            policy_status=data.get("policy_status", "UNKNOWN"),
            evidence_count=len(evidence),
            verified_count=verified_count,
            risk_count=len(data.get("risks", [])),
            approver=data.get("approver", ""),
            approved_at=data.get("approved_at", ""),
            execution_status=data.get("execution_status", "UNKNOWN"),
        )

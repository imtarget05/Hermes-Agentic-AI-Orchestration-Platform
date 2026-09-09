"""Research report template — sources, findings, comparison."""
from __future__ import annotations

from .base import BaseTemplate
from . import register_template
from ..models import ReportSection, ReportTier, ReportType


@register_template("research")
class ResearchTemplate(BaseTemplate):
    def report_type(self) -> ReportType:
        return ReportType.RESEARCH

    def required_sections(self) -> list[str]:
        return [
            "Executive Summary",
            "Research Question",
            "Scope",
            "Sources",
            "Findings",
            "Comparison",
            "Contradictions",
            "Verification",
            "Recommendation",
            "Uncertainty",
            "Evidence",
            "Audit Trail",
        ]

    def build_sections(self, data: dict) -> list[ReportSection]:
        sections = []
        order = 0

        order += 1
        sections.append(ReportSection(
            title="Executive Summary",
            tier=ReportTier.EXECUTIVE,
            content=data.get("executive_summary", ""),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Research Question",
            tier=ReportTier.EXECUTIVE,
            content=data.get("research_question", "N/A"),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Scope",
            tier=ReportTier.EXECUTIVE,
            content=data.get("scope", "N/A"),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Sources",
            tier=ReportTier.EVIDENCE,
            content=self._build_sources(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Findings",
            tier=ReportTier.EVIDENCE,
            content=self._build_findings(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Comparison",
            tier=ReportTier.EVIDENCE,
            content=data.get("comparison", "No comparison available."),
            table_data=data.get("comparison_table", []),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Contradictions",
            tier=ReportTier.EVIDENCE,
            content=self._build_contradictions(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Verification",
            tier=ReportTier.AUDIT,
            content=self._build_verification(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Recommendation",
            tier=ReportTier.EXECUTIVE,
            content=data.get("recommendation", "No recommendation."),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Uncertainty",
            tier=ReportTier.EVIDENCE,
            content=self._build_uncertainty(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Evidence",
            tier=ReportTier.EVIDENCE,
            content=self._build_evidence_section(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Audit Trail",
            tier=ReportTier.AUDIT,
            content=self._build_audit_trail(data),
            order=order,
        ))

        return sections

    def _build_sources(self, data: dict) -> str:
        sources = data.get("sources", [])
        if not sources:
            return "No sources listed."
        lines = []
        for i, s in enumerate(sources, 1):
            if isinstance(s, dict):
                lines.append(f"[S-{i:02d}] {s.get('name', 'Source')}")
                lines.append(f"  Type: {s.get('type', 'N/A')}")
                lines.append(f"  Freshness: {s.get('freshness', 'N/A')}")
                lines.append(f"  Confidence: {s.get('confidence', 'N/A')}")
            else:
                lines.append(f"[S-{i:02d}] {s}")
        return "\n".join(lines)

    def _build_findings(self, data: dict) -> str:
        findings = data.get("findings", [])
        if not findings:
            return "No findings recorded."
        lines = []
        for i, f in enumerate(findings, 1):
            if isinstance(f, dict):
                lines.append(f"[F-{i:02d}] {f.get('finding', 'Finding')}")
                lines.append(f"  Evidence: {f.get('evidence', 'N/A')}")
                lines.append(f"  Confidence: {f.get('confidence', 'N/A')}")
            else:
                lines.append(f"[F-{i:02d}] {f}")
        return "\n".join(lines)

    def _build_contradictions(self, data: dict) -> str:
        contradictions = data.get("contradictions", [])
        if not contradictions:
            return "No contradictions found."
        lines = []
        for c in contradictions:
            lines.append(f"- {c}")
        return "\n".join(lines)

    def _build_verification(self, data: dict) -> str:
        verified = data.get("verified_count", 0)
        total = data.get("source_count", 0)
        return f"""Sources: {total}
Verified: {verified}
Verification Status: {'COMPLETE' if verified == total else 'PARTIAL'}"""

    def _build_uncertainty(self, data: dict) -> str:
        known = data.get("known", [])
        inferred = data.get("inferred", [])
        uncertain = data.get("uncertain", [])

        lines = ["KNOWN:"]
        lines.extend(f"  ✓ {k}" for k in known) if known else lines.append("  (none)")

        lines.append("\nINFERRED:")
        lines.extend(f"  ~ {i}" for i in inferred) if inferred else lines.append("  (none)")

        lines.append("\nUNCERTAIN:")
        lines.extend(f"  ? {u}" for u in uncertain) if uncertain else lines.append("  (none)")

        return "\n".join(lines)

    def _build_evidence_section(self, data: dict) -> str:
        evidence = self._extract_evidence(data)
        if not evidence:
            return "No evidence collected."
        lines = []
        for e in evidence:
            lines.append(f"[{e.id}] {e.claim}")
            lines.append(f"  Source: {e.source}")
            lines.append(f"  Agent: {e.agent}")
            lines.append(f"  Verified: {e.verification}")
        return "\n".join(lines)

    def _build_audit_trail(self, data: dict) -> str:
        trail = data.get("audit_trail", [])
        if not trail:
            return "No audit events recorded."
        lines = []
        for event in trail:
            lines.append(f"[{event.get('timestamp', '')}] {event.get('actor', '')}: {event.get('action', '')}")
        return "\n".join(lines)

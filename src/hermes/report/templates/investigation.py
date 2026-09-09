"""Investigation report template — agent contributions, verification."""
from __future__ import annotations

from .base import BaseTemplate
from . import register_template
from ..models import ReportSection, ReportTier, ReportType


@register_template("investigation")
class InvestigationTemplate(BaseTemplate):
    def report_type(self) -> ReportType:
        return ReportType.INVESTIGATION

    def required_sections(self) -> list[str]:
        return [
            "Executive Summary",
            "Investigation Question",
            "Agent Contributions",
            "Findings",
            "Conflicting Findings",
            "Independent Verification",
            "Aggregated Conclusion",
            "Decision",
            "Evidence Map",
            "Remaining Uncertainty",
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
            title="Investigation Question",
            tier=ReportTier.EXECUTIVE,
            content=data.get("question", "N/A"),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Agent Contributions",
            tier=ReportTier.EVIDENCE,
            content=self._build_agent_contributions(data),
            table_data=self._build_agent_table(data),
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
            title="Conflicting Findings",
            tier=ReportTier.EVIDENCE,
            content=self._build_conflicts(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Independent Verification",
            tier=ReportTier.AUDIT,
            content=self._build_verification(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Aggregated Conclusion",
            tier=ReportTier.EXECUTIVE,
            content=data.get("conclusion", "N/A"),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Decision",
            tier=ReportTier.EXECUTIVE,
            content=data.get("decision_summary", "N/A"),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Evidence Map",
            tier=ReportTier.EVIDENCE,
            content=self._build_evidence_map(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Remaining Uncertainty",
            tier=ReportTier.EVIDENCE,
            content=data.get("uncertainty", "N/A"),
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

    def _build_agent_contributions(self, data: dict) -> str:
        agents = data.get("agent_contributions", [])
        if not agents:
            return "No agent contributions recorded."
        lines = []
        for a in agents:
            if isinstance(a, dict):
                lines.append(f"Agent: {a.get('agent', 'Unknown')}")
                lines.append(f"  Finding: {a.get('finding', 'N/A')}")
                lines.append(f"  Confidence: {a.get('confidence', 'N/A')}")
                lines.append(f"  Status: {a.get('status', 'N/A')}")
                lines.append("")
        return "\n".join(lines)

    def _build_agent_table(self, data: dict) -> list[list[str]]:
        headers = ["Agent", "Finding", "Confidence", "Status"]
        rows = [headers]
        for a in data.get("agent_contributions", []):
            if isinstance(a, dict):
                rows.append([
                    a.get("agent", "Unknown"),
                    a.get("finding", "N/A")[:40],
                    a.get("confidence", "N/A"),
                    a.get("status", "N/A"),
                ])
        return rows

    def _build_findings(self, data: dict) -> str:
        findings = data.get("findings", [])
        if not findings:
            return "No findings recorded."
        lines = []
        for i, f in enumerate(findings, 1):
            lines.append(f"{i}. {f}")
        return "\n".join(lines)

    def _build_conflicts(self, data: dict) -> str:
        conflicts = data.get("conflicts", [])
        if not conflicts:
            return "No conflicting findings."
        lines = []
        for c in conflicts:
            lines.append(f"- {c}")
        return "\n".join(lines)

    def _build_verification(self, data: dict) -> str:
        return f"""Verification Status: {data.get('verification_status', 'PENDING')}
Verified By: {data.get('verified_by', 'N/A')}
Conflicts Resolved: {data.get('conflicts_resolved', 'N/A')}"""

    def _build_evidence_map(self, data: dict) -> str:
        evidence = self._extract_evidence(data)
        if not evidence:
            return "No evidence mapped."
        lines = []
        for e in evidence:
            lines.append(f"[{e.id}] {e.claim} → {e.source}")
        return "\n".join(lines)

    def _build_audit_trail(self, data: dict) -> str:
        trail = data.get("audit_trail", [])
        if not trail:
            return "No audit events recorded."
        lines = []
        for event in trail:
            lines.append(f"[{event.get('timestamp', '')}] {event.get('actor', '')}: {event.get('action', '')}")
        return "\n".join(lines)

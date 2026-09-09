"""System maintenance report template — incident, impact, action, rollback."""
from __future__ import annotations

from .base import BaseTemplate
from . import register_template
from ..models import ReportSection, ReportTier, ReportType


@register_template("maintenance")
class MaintenanceTemplate(BaseTemplate):
    def report_type(self) -> ReportType:
        return ReportType.MAINTENANCE

    def required_sections(self) -> list[str]:
        return [
            "Executive Decision",
            "Incident / Request",
            "Affected Resources",
            "Current State",
            "Impact",
            "Proposed Action",
            "Alternatives",
            "Risk Analysis",
            "Policy Check",
            "Approval",
            "Execution",
            "Verification",
            "Rollback / Recovery",
            "Audit Trail",
        ]

    def build_sections(self, data: dict) -> list[ReportSection]:
        sections = []
        order = 0

        order += 1
        sections.append(ReportSection(
            title="Executive Decision",
            tier=ReportTier.EXECUTIVE,
            content=self._build_executive_decision(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Incident / Request",
            tier=ReportTier.EXECUTIVE,
            content=self._build_incident(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Affected Resources",
            tier=ReportTier.EVIDENCE,
            content=self._build_affected_resources(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Current State",
            tier=ReportTier.EVIDENCE,
            content=self._build_current_state(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Impact",
            tier=ReportTier.EVIDENCE,
            content=self._build_impact(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Proposed Action",
            tier=ReportTier.EXECUTIVE,
            content=self._build_proposed_action(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Alternatives",
            tier=ReportTier.EVIDENCE,
            content=self._build_alternatives(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Risk Analysis",
            tier=ReportTier.EVIDENCE,
            content=self._build_risk_analysis(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Policy Check",
            tier=ReportTier.AUDIT,
            content=self._build_policy_check(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Approval",
            tier=ReportTier.AUDIT,
            content=self._build_approval(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Execution",
            tier=ReportTier.AUDIT,
            content=self._build_execution(data),
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
            title="Rollback / Recovery",
            tier=ReportTier.AUDIT,
            content=self._build_rollback(data),
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

    def _build_executive_decision(self, data: dict) -> str:
        return f"""Action: {data.get('action', 'N/A')}
Affected: {data.get('affected_service', 'N/A')}
Risk Level: {data.get('risk_level', 'MEDIUM')}
Decision: {data.get('decision', 'PENDING')}"""

    def _build_incident(self, data: dict) -> str:
        return f"""Type: {data.get('incident_type', 'N/A')}
Description: {data.get('description', 'N/A')}
Detected At: {data.get('detected_at', 'N/A')}"""

    def _build_affected_resources(self, data: dict) -> str:
        resources = data.get('affected_resources', [])
        if not resources:
            return "No resources specified."
        return "\n".join(f"• {r}" for r in resources)

    def _build_current_state(self, data: dict) -> str:
        return data.get('current_state', 'State not assessed.')

    def _build_impact(self, data: dict) -> str:
        return f"""Service Impact: {data.get('service_impact', 'N/A')}
User Impact: {data.get('user_impact', 'N/A')}
Duration: {data.get('duration', 'N/A')}"""

    def _build_proposed_action(self, data: dict) -> str:
        return data.get('proposed_action', 'No action proposed.')

    def _build_alternatives(self, data: dict) -> str:
        alternatives = data.get('alternatives', [])
        if not alternatives:
            return "No alternatives evaluated."
        lines = []
        for i, alt in enumerate(alternatives, 1):
            if isinstance(alt, dict):
                lines.append(f"{i}. {alt.get('name', 'Alternative')}: {alt.get('description', '')}")
                if alt.get('recommended'):
                    lines.append("   ★ RECOMMENDED")
            else:
                lines.append(f"{i}. {alt}")
        return "\n".join(lines)

    def _build_risk_analysis(self, data: dict) -> str:
        return f"""Risk Level: {data.get('risk_level', 'MEDIUM')}
Mitigation: {data.get('mitigation', 'N/A')}"""

    def _build_policy_check(self, data: dict) -> str:
        requires_approval = data.get('requires_approval', True)
        return f"""Approval Required: {'Yes' if requires_approval else 'No'}
Policy Status: {data.get('policy_status', 'UNKNOWN')}"""

    def _build_approval(self, data: dict) -> str:
        return f"""Status: {data.get('decision', 'PENDING')}
Approver: {data.get('approver', 'Pending')}
Date: {data.get('approved_at', 'N/A')}"""

    def _build_execution(self, data: dict) -> str:
        return f"""Status: {data.get('execution_status', 'NOT STARTED')}
Started: {data.get('execution_started', 'N/A')}
Completed: {data.get('execution_completed', 'N/A')}"""

    def _build_verification(self, data: dict) -> str:
        return f"""Verification Status: {data.get('verification_status', 'PENDING')}
Health Check: {data.get('health_check', 'N/A')}"""

    def _build_rollback(self, data: dict) -> str:
        return f"""Rollback Plan: {data.get('rollback_plan', 'N/A')}
Recovery Time: {data.get('recovery_time', 'N/A')}"""

    def _build_audit_trail(self, data: dict) -> str:
        trail = data.get('audit_trail', [])
        if not trail:
            return "No audit events recorded."
        lines = []
        for event in trail:
            lines.append(f"[{event.get('timestamp', '')}] {event.get('actor', '')}: {event.get('action', '')}")
        return "\n".join(lines)

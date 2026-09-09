"""Financial report template — transaction details, policy check, impact."""
from __future__ import annotations

from .base import BaseTemplate
from . import register_template
from ..models import ReportSection, ReportTier, ReportType


@register_template("financial")
class FinancialTemplate(BaseTemplate):
    def report_type(self) -> ReportType:
        return ReportType.FINANCIAL

    def required_sections(self) -> list[str]:
        return [
            "Executive Decision",
            "Transaction Details",
            "Request Context",
            "Policy Check",
            "Financial Impact",
            "Risk Assessment",
            "Recommendation",
            "Approval",
            "Execution",
            "Verification",
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
            title="Transaction Details",
            tier=ReportTier.EXECUTIVE,
            content=self._build_transaction_details(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Request Context",
            tier=ReportTier.EVIDENCE,
            content=self._build_request_context(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Policy Check",
            tier=ReportTier.EVIDENCE,
            content=self._build_policy_check(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Financial Impact",
            tier=ReportTier.EVIDENCE,
            content=self._build_financial_impact(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Risk Assessment",
            tier=ReportTier.EVIDENCE,
            content=self._build_risk_assessment(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Recommendation",
            tier=ReportTier.EXECUTIVE,
            content=self._build_recommendation(data),
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
            title="Audit Trail",
            tier=ReportTier.AUDIT,
            content=self._build_audit_trail(data),
            order=order,
        ))

        return sections

    def _build_executive_decision(self, data: dict) -> str:
        return f"""Request: {data.get('request_id', 'N/A')}
Amount: {data.get('amount', 0):,.0f} VND
Reason: {data.get('reason', 'N/A')}
Decision: {data.get('decision', 'PENDING')}
Confidence: {data.get('confidence', 'MEDIUM')}"""

    def _build_transaction_details(self, data: dict) -> str:
        return f"""Transaction ID: {data.get('transaction_id', 'N/A')}
Type: {data.get('transaction_type', 'N/A')}
Date: {data.get('transaction_date', 'N/A')}
Amount: {data.get('amount', 0):,.0f} VND
Customer: {data.get('customer', 'N/A')}"""

    def _build_request_context(self, data: dict) -> str:
        return data.get('context', 'No context provided.')

    def _build_policy_check(self, data: dict) -> str:
        checks = data.get('policy_checks', [])
        if not checks:
            return "No policy checks performed."
        lines = []
        for check in checks:
            status = "✓" if check.get('passed', False) else "✗"
            lines.append(f"{status} {check.get('name', 'Check')}")
        return "\n".join(lines)

    def _build_financial_impact(self, data: dict) -> str:
        return f"""Gross Amount: {data.get('gross_amount', 0):,.0f} VND
Fee Loss: {data.get('fee_loss', 0):,.0f} VND
Net Impact: {data.get('net_impact', 0):,.0f} VND"""

    def _build_risk_assessment(self, data: dict) -> str:
        risks = data.get('risks', [])
        if not risks:
            return "No significant risks identified."
        return "\n".join(f"- {r}" for r in risks)

    def _build_recommendation(self, data: dict) -> str:
        return f"""Recommendation: {data.get('recommendation', 'PENDING')}
Reason: {data.get('recommendation_reason', 'N/A')}"""

    def _build_approval(self, data: dict) -> str:
        return f"""Status: {data.get('decision', 'PENDING')}
Approver: {data.get('approver', 'Pending')}
Date: {data.get('approved_at', 'N/A')}"""

    def _build_execution(self, data: dict) -> str:
        return f"""Status: {data.get('execution_status', 'NOT STARTED')}
Refund API: {data.get('api_status', 'N/A')}"""

    def _build_verification(self, data: dict) -> str:
        return f"""Verification Status: {data.get('verification_status', 'PENDING')}
Verified By: {data.get('verified_by', 'N/A')}"""

    def _build_audit_trail(self, data: dict) -> str:
        trail = data.get('audit_trail', [])
        if not trail:
            return "No audit events recorded."
        lines = []
        for event in trail:
            lines.append(f"[{event.get('timestamp', '')}] {event.get('actor', '')}: {event.get('action', '')}")
        return "\n".join(lines)

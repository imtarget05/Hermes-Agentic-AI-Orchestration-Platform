"""Procurement report template — vendor comparison, quotes, recommendation."""
from __future__ import annotations

from ..models import ReportSection, ReportTier, ReportType
from . import register_template
from .base import BaseTemplate


@register_template("procurement")
class ProcurementTemplate(BaseTemplate):
    def report_type(self) -> ReportType:
        return ReportType.PROCUREMENT

    def required_sections(self) -> list[str]:
        return [
            "Executive Decision",
            "Purchase Request",
            "Vendor Comparison",
            "Detailed Quotes",
            "Specification Evaluation",
            "Cost Analysis",
            "Recommendation",
            "Evidence",
            "Risks",
            "Approval",
            "Verification",
            "Audit Trail",
        ]

    def build_sections(self, data: dict) -> list[ReportSection]:
        sections = []
        order = 0

        # 1. Executive Decision
        order += 1
        sections.append(ReportSection(
            title="Executive Decision",
            tier=ReportTier.EXECUTIVE,
            content=self._build_executive_decision(data),
            order=order,
        ))

        # 2. Purchase Request
        order += 1
        sections.append(ReportSection(
            title="Purchase Request",
            tier=ReportTier.EXECUTIVE,
            content=self._build_purchase_request(data),
            order=order,
        ))

        # 3. Vendor Comparison (table)
        order += 1
        sections.append(ReportSection(
            title="Vendor Comparison",
            tier=ReportTier.EVIDENCE,
            table_data=self._build_vendor_table(data),
            evidence_ids=[e.id for e in self._extract_evidence(data)],
            order=order,
        ))

        # 4. Detailed Quotes
        order += 1
        sections.append(ReportSection(
            title="Detailed Quotes",
            tier=ReportTier.EVIDENCE,
            content=self._build_detailed_quotes(data),
            order=order,
        ))

        # 5. Specification Evaluation
        order += 1
        sections.append(ReportSection(
            title="Specification Evaluation",
            tier=ReportTier.EVIDENCE,
            content=self._build_spec_evaluation(data),
            table_data=self._build_spec_table(data),
            order=order,
        ))

        # 6. Cost Analysis
        order += 1
        sections.append(ReportSection(
            title="Cost Analysis",
            tier=ReportTier.EVIDENCE,
            content=self._build_cost_analysis(data),
            order=order,
        ))

        # 7. Recommendation
        order += 1
        sections.append(ReportSection(
            title="Recommendation",
            tier=ReportTier.EXECUTIVE,
            content=self._build_recommendation(data),
            order=order,
        ))

        # 8. Evidence
        order += 1
        sections.append(ReportSection(
            title="Evidence",
            tier=ReportTier.EVIDENCE,
            content=self._build_evidence_section(data),
            evidence_ids=[e.id for e in self._extract_evidence(data)],
            order=order,
        ))

        # 9. Risks
        order += 1
        sections.append(ReportSection(
            title="Risks",
            tier=ReportTier.EVIDENCE,
            content=self._build_risks(data),
            order=order,
        ))

        # 10. Approval
        order += 1
        sections.append(ReportSection(
            title="Approval",
            tier=ReportTier.AUDIT,
            content=self._build_approval(data),
            order=order,
        ))

        # 11. Verification
        order += 1
        sections.append(ReportSection(
            title="Verification",
            tier=ReportTier.AUDIT,
            content=self._build_verification(data),
            order=order,
        ))

        # 12. Audit Trail
        order += 1
        sections.append(ReportSection(
            title="Audit Trail",
            tier=ReportTier.AUDIT,
            content=self._build_audit_trail(data),
            order=order,
        ))

        return sections

    def _build_executive_decision(self, data: dict) -> str:
        rec = data.get("recommendation", {})
        if isinstance(rec, str):
            import json
            try:
                rec = json.loads(rec)
            except Exception:
                rec = {"vendor": "N/A", "total_cost": 0}

        vendor = rec.get("vendor", "N/A") if isinstance(rec, dict) else "N/A"
        total = rec.get("total_cost", 0) if isinstance(rec, dict) else 0
        confidence = data.get("confidence", "HIGH")
        policy = data.get("policy_status", "COMPLIANT")
        decision = data.get("decision", "PENDING")

        return f"""Recommended Vendor: {vendor}
Total Cost: {total:,.0f} VND
Decision Confidence: {confidence}
Policy Status: {policy}
Approval: {decision}"""

    def _build_purchase_request(self, data: dict) -> str:
        request = data.get("request", "")
        spec = data.get("spec", "")
        quantity = data.get("quantity", "")
        lines = [f"Request: {request}"]
        if quantity:
            lines.append(f"Quantity: {quantity}")
        if spec:
            lines.append(f"Specification: {spec}")
        return "\n".join(lines)

    def _build_vendor_table(self, data: dict) -> list[list[str]]:
        headers = ["Vendor", "Model", "Unit Price", "Qty", "Total", "Spec", "Warranty", "Status"]
        rows = [headers]
        for q in data.get("quotes", []):
            rows.append([
                q.get("vendor", ""),
                q.get("model", ""),
                f"${q.get('unit_price', 0):,.0f}",
                str(q.get("quantity", 0)),
                f"${q.get('total', 0):,.0f}",
                "OK" if q.get("meets_spec", True) else "FAIL",
                f"{q.get('warranty_years', 0)}y",
                "RECOMMENDED" if q.get("is_recommended") else "Eligible",
            ])
        return rows

    def _build_detailed_quotes(self, data: dict) -> str:
        lines = []
        for q in data.get("quotes", []):
            vendor = q.get("vendor", "Unknown")
            lines.append(f"{vendor}:")
            lines.append(f"  Unit Price: ${q.get('unit_price', 0):,.0f}")
            lines.append(f"  Quantity: {q.get('quantity', 0)}")
            lines.append(f"  Total: ${q.get('total', 0):,.0f}")
            lines.append(f"  Quote Date: {q.get('quote_date', 'N/A')}")
            lines.append(f"  Source: {q.get('source_uri', 'N/A')}")
            lines.append("")
        return "\n".join(lines) if lines else "No quotes available."

    def _build_spec_evaluation(self, data: dict) -> str:
        spec_scores = data.get("spec_scores", {})
        if not spec_scores:
            return "Specification evaluation not available."
        lines = []
        for vendor, score in spec_scores.items():
            meets = "✓ Meets" if score.get("meets_minimum", False) else "✗ Does not meet"
            lines.append(f"{vendor}: {meets} (Score: {score.get('score', 0):.1f}/100)")
        return "\n".join(lines)

    def _build_spec_table(self, data: dict) -> list[list[str]]:
        headers = ["Vendor", "Score", "Meets Minimum", "Notes"]
        rows = [headers]
        for vendor, score in data.get("spec_scores", {}).items():
            rows.append([
                vendor,
                f"{score.get('score', 0):.1f}",
                "YES" if score.get("meets_minimum", False) else "NO",
                score.get("notes", "")[:30],
            ])
        return rows

    def _build_cost_analysis(self, data: dict) -> str:
        quotes = data.get("quotes", [])
        if not quotes:
            return "Cost analysis not available."

        totals = [q.get("total", 0) for q in quotes if q.get("total", 0) > 0]
        if not totals:
            return "No valid cost data."

        min_cost = min(totals)
        max_cost = max(totals)
        avg_cost = sum(totals) / len(totals)

        return f"""Cost Range: ${min_cost:,.0f} — ${max_cost:,.0f}
Average Cost: ${avg_cost:,.0f}
Number of Vendors: {len(totals)}"""

    def _build_recommendation(self, data: dict) -> str:
        rec = data.get("recommendation", {})
        if isinstance(rec, str):
            import json
            try:
                rec = json.loads(rec)
            except Exception:
                rec = {"vendor": "N/A", "reasons": []}

        vendor = rec.get("vendor", "N/A") if isinstance(rec, dict) else "N/A"
        reasons = rec.get("reasons", []) if isinstance(rec, dict) else []

        lines = [f"Recommended: {vendor}", ""]
        if reasons:
            lines.append("Reasons:")
            for r in reasons:
                claim = r.get("claim", "") if isinstance(r, dict) else str(r)
                evidence = r.get("evidence_ref", "") if isinstance(r, dict) else ""
                lines.append(f"  - {claim}")
                if evidence:
                    lines.append(f"    Evidence: {evidence}")
        return "\n".join(lines)

    def _build_evidence_section(self, data: dict) -> str:
        evidence = self._extract_evidence(data)
        if not evidence:
            return "No evidence collected."
        lines = []
        for e in evidence:
            lines.append(f"[{e.id}]")
            lines.append(f"  Claim: {e.claim}")
            lines.append(f"  Source: {e.source}")
            lines.append(f"  Agent: {e.agent}")
            lines.append(f"  Verified: {e.verification}")
            lines.append("")
        return "\n".join(lines)

    def _build_risks(self, data: dict) -> str:
        risks = data.get("risks", [])
        if not risks:
            return "No significant risks identified."
        lines = []
        for r in risks:
            if isinstance(r, dict):
                lines.append(f"- {r.get('description', r.get('risk', ''))}")
                if r.get("severity"):
                    lines.append(f"  Severity: {r['severity']}")
            else:
                lines.append(f"- {r}")
        return "\n".join(lines)

    def _build_approval(self, data: dict) -> str:
        approver = data.get("approver", "Pending")
        approved_at = data.get("approved_at", "")
        decision = data.get("decision", "PENDING")
        lines = [
            f"Status: {decision}",
            f"Approver: {approver}",
        ]
        if approved_at:
            lines.append(f"Date: {approved_at}")
        return "\n".join(lines)

    def _build_verification(self, data: dict) -> str:
        evidence = self._extract_evidence(data)
        verified = sum(1 for e in evidence if e.verification == "VERIFIED")
        total = len(evidence)
        return f"""Evidence Items: {total}
Verified: {verified}
Verification Status: {'COMPLETE' if verified == total else 'PARTIAL'}"""

    def _build_audit_trail(self, data: dict) -> str:
        trail = data.get("audit_trail", [])
        if not trail:
            return "No audit events recorded."
        lines = []
        for event in trail:
            timestamp = event.get("timestamp", "")
            action = event.get("action", "")
            actor = event.get("actor", "")
            lines.append(f"[{timestamp}] {actor}: {action}")
        return "\n".join(lines)

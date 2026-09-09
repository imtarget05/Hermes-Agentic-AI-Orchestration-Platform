"""Hermes Report Template System — multi-case PDF generation.

Architecture:
Workflow Result → NormalizedReport → Template → Renderer → PDF

Supported report types:
- procurement: Vendor comparison, quotes, recommendation
- financial: Transaction details, policy check, impact
- maintenance: Incident, impact, action, rollback
- research: Sources, findings, comparison
- investigation: Agent contributions, verification
- workflow: Task graph, execution status
"""
from __future__ import annotations

from .models import (
    DecisionCard,
    EvidenceItem,
    NormalizedReport,
    ReportSection,
    ReportTier,
    ReportType,
)
from .renderer import PDFRenderer


def generate_report(
    report_type: str,
    data: dict,
    workflow_id: str = "",
    task_id: str = "",
) -> bytes:
    """Generate PDF report from workflow data.

    Args:
        report_type: One of procurement, financial, maintenance, research,
                     investigation, workflow
        data: Workflow-specific data dictionary
        workflow_id: Optional workflow identifier
        task_id: Optional task identifier

    Returns:
        PDF bytes ready to save/send
    """
    from .templates import get_template

    template = get_template(report_type)
    report = template.build_report(data, workflow_id, task_id)
    renderer = PDFRenderer()
    return renderer.render(report)

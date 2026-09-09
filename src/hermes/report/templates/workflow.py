"""Workflow execution report template — task graph, execution status."""
from __future__ import annotations

from .base import BaseTemplate
from . import register_template
from ..models import ReportSection, ReportTier, ReportType


@register_template("workflow")
class WorkflowTemplate(BaseTemplate):
    def report_type(self) -> ReportType:
        return ReportType.WORKFLOW

    def required_sections(self) -> list[str]:
        return [
            "Executive Summary",
            "Workflow Summary",
            "Task Graph",
            "Parallel Tasks",
            "Completed Tasks",
            "Failed Tasks",
            "Retries",
            "Timeouts",
            "Verification",
            "Final Result",
            "Duration",
            "Token / Cost",
            "Audit Events",
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
            title="Workflow Summary",
            tier=ReportTier.EXECUTIVE,
            content=self._build_workflow_summary(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Task Graph",
            tier=ReportTier.EVIDENCE,
            content=self._build_task_graph(data),
            table_data=self._build_task_table(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Parallel Tasks",
            tier=ReportTier.EVIDENCE,
            content=self._build_parallel_tasks(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Completed Tasks",
            tier=ReportTier.EVIDENCE,
            content=self._build_completed_tasks(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Failed Tasks",
            tier=ReportTier.EVIDENCE,
            content=self._build_failed_tasks(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Retries",
            tier=ReportTier.AUDIT,
            content=self._build_retries(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Timeouts",
            tier=ReportTier.AUDIT,
            content=self._build_timeouts(data),
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
            title="Final Result",
            tier=ReportTier.EXECUTIVE,
            content=data.get("final_result", "N/A"),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Duration",
            tier=ReportTier.AUDIT,
            content=self._build_duration(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Token / Cost",
            tier=ReportTier.AUDIT,
            content=self._build_token_cost(data),
            order=order,
        ))

        order += 1
        sections.append(ReportSection(
            title="Audit Events",
            tier=ReportTier.AUDIT,
            content=self._build_audit_events(data),
            order=order,
        ))

        return sections

    def _build_workflow_summary(self, data: dict) -> str:
        return f"""Workflow ID: {data.get('workflow_id', 'N/A')}
Status: {data.get('status', 'N/A')}
Total Tasks: {data.get('total_tasks', 0)}
Completed: {data.get('completed_tasks', 0)}
Failed: {data.get('failed_tasks', 0)}"""

    def _build_task_graph(self, data: dict) -> str:
        tasks = data.get("tasks", [])
        if not tasks:
            return "No tasks recorded."
        lines = []
        for t in tasks:
            status_icon = {
                "completed": "✅",
                "failed": "❌",
                "running": "⏳",
                "queued": "⬜",
                "retrying": "🔄",
            }.get(t.get("status", ""), "❓")
            lines.append(f"{status_icon} {t.get('task_id', 'N/A')} ({t.get('task_type', 'N/A')})")
        return "\n".join(lines)

    def _build_task_table(self, data: dict) -> list[list[str]]:
        headers = ["Task ID", "Type", "Status", "Duration", "Retries"]
        rows = [headers]
        for t in data.get("tasks", []):
            rows.append([
                t.get("task_id", "N/A"),
                t.get("task_type", "N/A"),
                t.get("status", "N/A"),
                f"{t.get('duration', 0):.1f}s",
                str(t.get("retries", 0)),
            ])
        return rows

    def _build_parallel_tasks(self, data: dict) -> str:
        parallel = data.get("parallel_tasks", [])
        if not parallel:
            return "No parallel tasks identified."
        return "Parallel tasks: " + ", ".join(parallel)

    def _build_completed_tasks(self, data: dict) -> str:
        completed = [t for t in data.get("tasks", []) if t.get("status") == "completed"]
        return f"Completed: {len(completed)} tasks"

    def _build_failed_tasks(self, data: dict) -> str:
        failed = [t for t in data.get("tasks", []) if t.get("status") == "failed"]
        if not failed:
            return "No failed tasks."
        lines = []
        for t in failed:
            lines.append(f"• {t.get('task_id', 'N/A')}: {t.get('error', 'Unknown error')}")
        return "\n".join(lines)

    def _build_retries(self, data: dict) -> str:
        retries = sum(t.get("retries", 0) for t in data.get("tasks", []))
        return f"Total retries: {retries}"

    def _build_timeouts(self, data: dict) -> str:
        timeouts = [t for t in data.get("tasks", []) if t.get("timed_out")]
        if not timeouts:
            return "No timeouts."
        return f"Timed out: {len(timeouts)} tasks"

    def _build_verification(self, data: dict) -> str:
        return f"""Verification Status: {data.get('verification_status', 'PENDING')}
Verified By: {data.get('verified_by', 'N/A')}"""

    def _build_duration(self, data: dict) -> str:
        total = data.get("total_duration", 0)
        return f"Total Duration: {total:.1f}s"

    def _build_token_cost(self, data: dict) -> str:
        tokens = data.get("total_tokens", 0)
        cost = data.get("estimated_cost", 0)
        return f"""Total Tokens: {tokens:,}
Estimated Cost: ${cost:.4f}"""

    def _build_audit_events(self, data: dict) -> str:
        events = data.get("audit_events", [])
        if not events:
            return "No audit events recorded."
        lines = []
        for event in events:
            lines.append(f"[{event.get('timestamp', '')}] {event.get('action', '')}")
        return "\n".join(lines)

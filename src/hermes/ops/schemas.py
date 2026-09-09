"""Business Operations Hub schemas (②) — connectors + attention items."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

OpsSourceKind = Literal["crm", "invoicing", "calendar", "inbox", "custom"]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class OpsSource(BaseModel):
    source_id: str = ""
    tenant_id: str = "default"
    kind: OpsSourceKind = "crm"
    name: str = ""
    enabled: bool = True
    config: dict = Field(default_factory=dict)
    connected_at: str = Field(default_factory=_now_iso)


class OpsAttentionItem(BaseModel):
    source_id: str = ""
    kind: OpsSourceKind = "crm"
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    summary: str = ""
    due_at: str = ""            # ISO datetime or empty
    action_hint: str = ""
    raw: dict = Field(default_factory=dict)


class OpsReport(BaseModel):
    tenant_id: str = "default"
    generated_at: str = Field(default_factory=_now_iso)
    items: list[OpsAttentionItem] = Field(default_factory=list)
    n_high: int = 0

    def to_text(self) -> str:
        if not self.items:
            return "✅ Không có gì cần chú ý ngay lúc này."
        lines = ["# 🔔 Operations attention"]
        by_last = {"critical": [], "high": [], "medium": [], "low": []}
        for it in self.items:
            by_last[it.severity].append(it)
        for sev in ("critical", "high", "medium", "low"):
            bucket = by_last[sev]
            if not bucket:
                continue
            lines.append(f"\n## {sev.upper()} ({len(bucket)})")
            for it in bucket:
                due = f" · hạn {it.due_at}" if it.due_at else ""
                lines.append(f"- {it.summary}{due}")
                if it.action_hint:
                    lines.append(f"    → {it.action_hint}")
        return "\n".join(lines)


_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def sort_by_severity(items: list[OpsAttentionItem]) -> list[OpsAttentionItem]:
    return sorted(items, key=lambda it: _SEVERITY_RANK.get(it.severity, 1),
                  reverse=True)
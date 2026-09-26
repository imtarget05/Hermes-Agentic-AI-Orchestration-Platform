"""Xero native connector — invoicing domain.

Fetches overdue/unpaid invoices from Xero and maps them to attention items.
Requires: HERMES_XERO_CLIENT_ID, HERMES_XERO_CLIENT_SECRET, HERMES_XERO_REFRESH_TOKEN,
plus HERMES_XERO_TENANT_ID for API calls.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime

from ..auth import OAuth2TokenManager, vendor_request
from ..schemas import OpsAttentionItem, OpsSource, OpsSourceKind

_XERO_BASE = "https://api.xero.com/api.xro/2.0"


def _map_invoices(payload: dict, source_id: str) -> list[OpsAttentionItem]:
    """Pure function: Xero API response → attention items.

    Maps invoice status/overdue days to severity:
      - PAID → skipped
      - OVERDUE by >30 days → critical
      - OVERDUE by 8-30 days → high
      - OVERDUE by 1-7 days → medium
      - AUTHORISED/DRAFT with due date within 7 days → medium
      - AUTHORISED/DRAFT otherwise → low
    """
    items: list[OpsAttentionItem] = []
    invoices = payload.get("Invoices", []) if isinstance(payload, dict) else []

    now = datetime.now(UTC)

    for inv in invoices:
        status = (inv.get("Status") or "").upper()
        if status == "PAID" or status == "DELETED" or status == "VOIDED":
            continue

        contact = inv.get("Contact", {}) or {}
        contact_name = contact.get("Name", "Unknown")
        invoice_num = inv.get("InvoiceNumber", inv.get("InvoiceID", "?"))
        amount_due = float(inv.get("AmountDue", 0) or 0)
        total = float(inv.get("Total", 0) or 0)

        # Determine severity based on overdue status
        severity = "low"
        due_str = inv.get("DueDateString") or inv.get("DueDate", "")
        is_overdue = status == "OVERDUE"

        if is_overdue:
            # Xero marks as OVERDUE; estimate days from due date if available
            days_overdue = _estimate_days_overdue(due_str, now)
            if days_overdue > 30 or amount_due > 10000:
                severity = "critical"
            elif days_overdue > 7:
                severity = "high"
            else:
                severity = "medium"
        elif due_str:
            due_date = _parse_xero_date(due_str)
            if due_date:
                days_until = (due_date - now).days
                if days_until < 0:
                    severity = "high"  # past due but not yet OVERDUE status
                elif days_until <= 7:
                    severity = "medium"

        summary = f"Hóa đơn #{invoice_num} — {contact_name}: {amount_due:,.0f} còn nợ (tổng {total:,.0f})"
        action_hint = f"Thu tiền / gửi nhắc nhở cho {contact_name}"

        items.append(OpsAttentionItem(
            source_id=source_id,
            kind="invoicing",
            severity=severity,
            summary=summary,
            due_at=due_str,
            action_hint=action_hint,
            raw=inv,
        ))

    return items


def _parse_xero_date(s: str) -> datetime | None:
    """Parse Xero date string, always returning UTC-aware datetime."""
    s = s.strip()
    if not s:
        return None
    # Xero epoch format: /Date(1700000000000+0000)/
    if s.startswith("/Date("):
        try:
            epoch_str = s.split("(")[1].split(")")[0]
            epoch_ms = int(epoch_str[:10])
            return datetime.fromtimestamp(epoch_ms, tz=UTC)
        except (ValueError, IndexError):
            return None
    # ISO format
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        # Ensure timezone-aware (assume UTC if naive)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return None


def _estimate_days_overdue(due_str: str, now: datetime) -> int:
    """Estimate days overdue from due date string."""
    due = _parse_xero_date(due_str)
    if due is None:
        return 15  # default to mid-range if unknown
    return max(0, (now - due).days)


class XeroConnector:
    """Native Xero connector using OAuth2 refresh-token flow."""

    kind: OpsSourceKind = "invoicing"

    def __init__(self, tenant_id: str = ""):
        self._manager = OAuth2TokenManager("xero")
        self._tenant_id = tenant_id or os.environ.get("HERMES_XERO_TENANT_ID", "")

    def collect(self, source: OpsSource) -> list[OpsAttentionItem]:
        if not self._manager.is_configured or not self._tenant_id:
            return []

        url = f"{_XERO_BASE}/Invoices"
        params = {"where": "AmountDue>0", "order": "DueDate ASC"}
        headers = {"Xero-Tenant-Id": self._tenant_id}

        try:
            payload = vendor_request(
                self._manager, "GET", url,
                params=params, extra_headers=headers,
            )
            return _map_invoices(payload, source.source_id)
        except Exception:
            return []


def xero_factory() -> XeroConnector | None:
    """Return XeroConnector if credentials are configured, else None."""
    if os.environ.get("HERMES_XERO_CLIENT_ID") and os.environ.get("HERMES_XERO_REFRESH_TOKEN"):
        return XeroConnector()
    return None


# Self-register on import
from . import register_vendor  # noqa: E402

register_vendor("invoicing", xero_factory)

"""Ops connectors — native vendor connectors + generic HTTP fallback.

Priority order when building a connector:
1. Native vendor connector (if OAuth credentials are configured via env vars)
2. Generic HTTP connector (if HERMES_{KIND}_API_URL is set)
3. Empty generic connector (no data, no error)
"""
from __future__ import annotations

import os
from typing import Callable, Protocol

import httpx

from .schemas import OpsAttentionItem, OpsSource, OpsSourceKind


class OpsConnector(Protocol):
    kind: OpsSourceKind

    def collect(self, source: OpsSource) -> list[OpsAttentionItem]: ...  # noqa: E704


class GenericConnector(OpsConnector):
    kind: OpsSourceKind = "custom"

    def collect(self, source: OpsSource) -> list[OpsAttentionItem]:
        fetch = getattr(self, "_fetch", None) or source.config.get("fetch")
        if not callable(fetch):
            return []
        out = fetch()
        items = []
        for it in out or []:
            items.append(OpsAttentionItem(
                source_id=source.source_id, kind=self.kind,
                severity=it.get("severity", "low"),
                summary=it.get("summary", ""),
                due_at=it.get("due_at", ""),
                action_hint=it.get("action_hint", ""),
                raw=it))
        return items


def _http_fetch(kind: str, base_url: str) -> list[dict]:
    if not base_url:
        return []
    try:
        resp = httpx.get(base_url, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        return resp.json()
    except Exception:  # noqa: BLE001
        return []


def _make_http_connector(kind: str, base_url: str | None = None) -> GenericConnector:
    if base_url is None:
        env_var = f"HERMES_{kind.upper()}_API_URL"
        base_url = os.environ.get(env_var, "")
    connector = GenericConnector()
    connector.kind = kind  # type: ignore[assignment]
    connector._fetch = lambda: _http_fetch(kind, base_url)  # type: ignore[attr-defined]
    return connector


def _crm_factory() -> GenericConnector:
    return _make_http_connector("crm")


def _invoicing_factory() -> GenericConnector:
    return _make_http_connector("invoicing")


def _calendar_factory() -> GenericConnector:
    return _make_http_connector("calendar")


def _inbox_factory() -> GenericConnector:
    return _make_http_connector("inbox")


_CONNECTOR_FACTORY: dict[str, Callable[[], OpsConnector]] = {
    "crm": _crm_factory,
    "invoicing": _invoicing_factory,
    "calendar": _calendar_factory,
    "inbox": _inbox_factory,
    "custom": lambda: GenericConnector(),
}


def _try_vendor_connector(kind: str) -> OpsConnector | None:
    """Return a native vendor connector if credentials are configured."""
    try:
        from . import vendors
        vendors._noop()  # ensure vendors are registered
        for conn in vendors.get_vendor_connectors(kind):
            return conn
    except Exception:
        pass
    return None


def build_connector(kind: str, source=None) -> OpsConnector:
    """Build a connector for the given source kind.

    Priority:
    1. Native vendor connector (if OAuth credentials present)
    2. Generic HTTP connector (with base_url from source config or env var)
    """
    # 1. Try native vendor connector first
    vendor_conn = _try_vendor_connector(kind)
    if vendor_conn is not None:
        return vendor_conn

    # 2. Fall back to generic HTTP connector
    base_url = None
    if source is not None:
        base_url = source.config.get("base_url", "")
    factory = _CONNECTOR_FACTORY.get(kind)
    if factory is None:
        factory = _crm_factory
    connector = factory()
    if base_url and isinstance(connector, GenericConnector):
        connector._fetch = lambda: _http_fetch(kind, base_url)  # type: ignore[attr-defined]
    return connector

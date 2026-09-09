"""Native vendor connectors for Ops Hub.

Each vendor module exports a factory function that returns an OpsConnector
when credentials are configured, or None when they are absent.
"""
from __future__ import annotations

from ..schemas import OpsSourceKind

# Registry of vendor factories per source kind
_VENDOR_FACTORIES: dict[OpsSourceKind, list] = {
    "crm": [],
    "invoicing": [],
    "calendar": [],
    "inbox": [],
}


def register_vendor(kind: OpsSourceKind, factory) -> None:
    """Register a vendor factory for a given source kind.

    Factory takes no args and returns an OpsConnector or None.
    """
    _VENDOR_FACTORIES.setdefault(kind, []).append(factory)


def get_vendor_connectors(kind: OpsSourceKind):
    """Yield configured vendor connectors for a source kind."""
    for factory in _VENDOR_FACTORIES.get(kind, []):
        try:
            conn = factory()
            if conn is not None:
                yield conn
        except Exception:
            continue


def _noop() -> None:
    """Import side-effect: ensure all vendor modules register themselves."""
    from . import xero  # noqa: F401

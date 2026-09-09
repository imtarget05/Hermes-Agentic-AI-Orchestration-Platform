"""Business Operations Hub package (②) — CRM/invoicing/calendar/inbox."""
from .auth import OAuth2TokenManager, vendor_request
from .connectors import (
    GenericConnector,
    build_connector,
)
from .hub import OpsHub
from .schemas import (
    OpsAttentionItem,
    OpsReport,
    OpsSource,
    sort_by_severity,
)

__all__ = [
    "GenericConnector",
    "OAuth2TokenManager",
    "OpsAttentionItem",
    "OpsHub",
    "OpsReport",
    "OpsSource",
    "build_connector",
    "sort_by_severity",
    "vendor_request",
]

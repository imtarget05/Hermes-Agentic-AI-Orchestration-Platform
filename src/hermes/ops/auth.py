"""OAuth2 token management for native vendor connectors.

Handles refresh-token flow per provider, caches access tokens in memory,
and auto-refreshes on expiry or 401 responses.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx


# ---- Provider token endpoints ----------------------------------------------

_TOKEN_ENDPOINTS: dict[str, str] = {
    "salesforce": "https://login.salesforce.com/services/oauth2/token",
    "xero": "https://identity.xero.com/connect/token",
    "google": "https://oauth2.googleapis.com/token",
    "microsoft": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
}


# ---- Token manager ---------------------------------------------------------

@dataclass
class OAuth2TokenManager:
    """Manages OAuth2 access tokens via refresh-token grant.

    Usage:
        mgr = OAuth2TokenManager("xero")
        token = mgr.get_token()  # valid access token, auto-refreshed
    """

    provider: str
    client_id: str = ""
    client_secret: str = ""
    refresh_token: str = ""
    token_url: str = ""
    _access_token: str = field(default="", repr=False)
    _expires_at: float = 0.0
    _buffer_s: float = 60.0  # refresh 60s before actual expiry

    def __post_init__(self) -> None:
        if not self.client_id:
            self.client_id = os.environ.get(f"HERMES_{self.provider.upper()}_CLIENT_ID", "")
        if not self.client_secret:
            self.client_secret = os.environ.get(f"HERMES_{self.provider.upper()}_CLIENT_SECRET", "")
        if not self.refresh_token:
            self.refresh_token = os.environ.get(f"HERMES_{self.provider.upper()}_REFRESH_TOKEN", "")
        if not self.token_url:
            self.token_url = _TOKEN_ENDPOINTS.get(self.provider, "")

    @property
    def is_configured(self) -> bool:
        """True if all required credentials are present."""
        return bool(self.client_id and self.client_secret and self.refresh_token and self.token_url)

    def get_token(self) -> str:
        """Return a valid access token, refreshing if needed."""
        if self._access_token and time.time() < (self._expires_at - self._buffer_s):
            return self._access_token
        self._refresh()
        return self._access_token

    def _refresh(self) -> None:
        """Exchange refresh token for a new access token."""
        if not self.is_configured:
            raise RuntimeError(f"OAuth2 not configured for provider: {self.provider}")

        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        # Xero requires Basic auth header instead of body params for some flows
        if self.provider == "xero":
            resp = httpx.post(self.token_url, data=data, timeout=15)
        else:
            resp = httpx.post(self.token_url, data=data, timeout=15)

        resp.raise_for_status()
        payload = resp.json()

        self._access_token = payload["access_token"]
        self._expires_at = time.time() + payload.get("expires_in", 1800)

    def auth_header(self) -> dict[str, str]:
        """Return Authorization header dict with current token."""
        return {"Authorization": f"Bearer {self.get_token()}"}


# ---- HTTP client helper ----------------------------------------------------

def vendor_request(
    manager: OAuth2TokenManager,
    method: str,
    url: str,
    *,
    params: dict | None = None,
    extra_headers: dict | None = None,
    max_retries: int = 2,
) -> dict | list:
    """Make an authenticated request to a vendor API with auto-retry on 401.

    Returns parsed JSON response. Raises on persistent failure.
    """
    headers = manager.auth_header()
    headers["Accept"] = "application/json"
    if extra_headers:
        headers.update(extra_headers)

    for attempt in range(max_retries + 1):
        resp = httpx.request(method, url, headers=headers, params=params, timeout=15)

        if resp.status_code == 401 and attempt < max_retries:
            # Token may have been revoked — force refresh
            manager._expires_at = 0.0
            headers = manager.auth_header()
            headers["Accept"] = "application/json"
            if extra_headers:
                headers.update(extra_headers)
            continue

        resp.raise_for_status()
        return resp.json()

    raise RuntimeError(f"vendor_request failed after {max_retries + 1} attempts: {url}")

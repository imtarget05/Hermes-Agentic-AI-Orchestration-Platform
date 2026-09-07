"""HERMES-11 — ACL-ready Memory Asset model + SafeMemoryClient (G-02 extension).

If an external memory/skill store is integrated, its assets are first-class
principals with explicit ACLs that map 1:1 onto Hermes' tenant/workflow
boundary. This module defines:

  * MemoryAsset  — the asset + its owner/team/tenant/visibility/ACLs
  * MemoryACL    — a single grant (grantee + permission)
  * ACLPolicy    — fail-closed authorization (default deny, tenant-scoped)
  * MemoryClient — protocol a real store implements
  * SafeMemoryClient — wrapper enforcing the two HERMES-11 invariants:
        1. content can never authorize a tool — only an APPROVED asset with an
           explicit ACL grant can, and even then the permission comes from the
           ACL, never from the text of the memory.
        2. retrieval returns content only when ACLPolicy.authorize permits the
           read (tenant boundary + ACL), so a cross-workflow read is impossible
           by default.

No store is integrated today, so the default client is an in-memory no-op.
The boundary is fully unit-testable without any external dependency.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}" if prefix else uuid.uuid4().hex


@dataclass
class MemoryACL:
    """A single grant on a memory asset (HERMES-11)."""
    grantee_type: str  # "tenant" | "team" | "user"
    grantee_id: str
    permission: str    # "read" | "write" | "authorize"

    def to_dict(self) -> dict[str, Any]:
        return {
            "grantee_type": self.grantee_type,
            "grantee_id": self.grantee_id,
            "permission": self.permission,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryACL":
        return cls(
            grantee_type=data.get("grantee_type", "user"),
            grantee_id=data.get("grantee_id", ""),
            permission=data.get("permission", "read"),
        )


@dataclass
class MemoryAsset:
    """A memory asset with an explicit owner + ACLs (HERMES-11).

    `visibility` is fail-closed: the default is "private" (owner-only). A
    cross-workflow read must be granted by an explicit ACL entry AND pass the
    tenant boundary — it never happens by default.
    """
    asset_id: str = field(default_factory=lambda: new_id("mem-"))
    kind: str = "chat_memory"      # chat_memory | skill | persona | document
    content: str = ""
    owner_id: str = ""
    team_id: str = ""
    tenant_id: str = ""
    visibility: str = "private"    # private | team | tenant | public
    tier: str = "raw_store"        # raw_store | reviewed | approved
    acls: list[MemoryACL] = field(default_factory=list)
    source_uri: str = ""
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "kind": self.kind,
            "content": self.content,
            "owner_id": self.owner_id,
            "team_id": self.team_id,
            "tenant_id": self.tenant_id,
            "visibility": self.visibility,
            "tier": self.tier,
            "acls": [a.to_dict() for a in self.acls],
            "source_uri": self.source_uri,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryAsset":
        return cls(
            asset_id=data.get("asset_id", new_id("mem-")),
            kind=data.get("kind", "chat_memory"),
            content=data.get("content", ""),
            owner_id=data.get("owner_id", ""),
            team_id=data.get("team_id", ""),
            tenant_id=data.get("tenant_id", ""),
            visibility=data.get("visibility", "private"),
            tier=data.get("tier", "raw_store"),
            acls=[MemoryACL.from_dict(a) for a in data.get("acls", [])],
            source_uri=data.get("source_uri", ""),
            created_at=data.get("created_at", _now()),
        )


class ACLPolicy:
    """Fail-closed authorization for memory assets (HERMES-11).

    Default posture: DENY. A read is allowed only when BOTH hold:
      (a) the principal is in the same tenant as the asset (tenant boundary),
      (b) the principal is the owner, OR an ACL entry grants the permission
          at the required scope (visibility).
    """

    @classmethod
    def authorize(
        cls,
        asset: MemoryAsset,
        principal: str,
        principal_tenant_id: str,
        wanted_perm: str = "read",
    ) -> bool:
        """Return True only if `principal` may `wanted_perm` on `asset`."""
        # Tenant boundary first: cross-tenant access is never allowed.
        if principal_tenant_id != asset.tenant_id:
            return False
        # Owner always has full access within their own tenant.
        if principal == asset.owner_id:
            return True
        # Private non-owner: denied.
        if asset.visibility == "private":
            return False
        # Explicit ACL grant required for any non-owner access.
        for acl in asset.acls:
            if acl.grantee_id == principal and acl.permission == wanted_perm:
                return True
        return False  # default deny


class MemoryClient(Protocol):
    """Protocol an external memory store implements (HERMES-11)."""

    def get(self, asset_id: str, principal: str, tenant_id: str) -> MemoryAsset | None: ...
    def put(self, asset: MemoryAsset) -> None: ...


class SafeMemoryClient:
    """Wrapper enforcing the HERMES-11 invariants over any MemoryClient.

    Invariant 1 — content never authorizes a tool. `authorize_tool` grants a
    tool permission only when the asset is APPROVED AND its ACL explicitly
    carries an "authorize" grant for the principal; the asset's text is never
    consulted. Invariant 2 — `get` returns the asset only when
    ACLPolicy.authorize permits the read (tenant boundary + ACL).
    """

    def __init__(self, backend: MemoryClient | None = None):
        self._backend = backend if backend is not None else _InMemoryStore()

    def get(self, asset_id: str, principal: str, tenant_id: str) -> MemoryAsset | None:
        asset = self._backend.get(asset_id, principal, tenant_id)
        if asset is None:
            return None
        if not ACLPolicy.authorize(asset, principal, tenant_id, "read"):
            return None  # fail-closed: no read without explicit authorization
        return asset

    def put(self, asset: MemoryAsset) -> None:
        self._backend.put(asset)

    def authorize_tool(
        self,
        asset_id: str,
        principal: str,
        tenant_id: str,
        tool_name: str,
    ) -> bool:
        """May `principal` invoke `tool_name` on this asset? (HERMES-11)

        Content of the memory is NEVER the source of authority. Only an
        APPROVED asset with an explicit "authorize" ACL grant can permit it.
        """
        asset = self._backend.get(asset_id, principal, tenant_id)
        if asset is None:
            return False
        # Tenant boundary + read access first.
        if not ACLPolicy.authorize(asset, principal, tenant_id, "read"):
            return False
        # Only APPROVED assets may carry tool-authorization grants.
        if asset.tier != "approved":
            return False
        # The permission comes from the ACL, never from the content.
        return any(
            acl.grantee_id == principal and acl.permission == "authorize"
            for acl in asset.acls
        )


class _InMemoryStore:
    """No-op reference backend (no external store integrated)."""

    def __init__(self) -> None:
        self._assets: dict[str, MemoryAsset] = {}

    def get(self, asset_id: str, principal: str, tenant_id: str) -> MemoryAsset | None:
        return self._assets.get(asset_id)

    def put(self, asset: MemoryAsset) -> None:
        self._assets[asset.asset_id] = asset

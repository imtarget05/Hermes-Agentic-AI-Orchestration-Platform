"""RBAC + Tenant Isolation models."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class Tenant:
    id: str
    name: str
    settings: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @classmethod
    def create(cls, id: str, name: str, settings: dict[str, Any] | None = None) -> "Tenant":
        return cls(id=id, name=name, settings=settings or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "settings": self.settings,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Tenant":
        return cls(
            id=data["id"],
            name=data["name"],
            settings=data.get("settings", {}),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
        )


@dataclass
class User:
    id: str
    tenant_id: str
    username: str
    email: str
    roles: list[str] = field(default_factory=list)
    is_active: bool = True
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @classmethod
    def create(
        cls,
        id: str,
        tenant_id: str,
        username: str,
        email: str,
        roles: list[str] | None = None,
        is_active: bool = True,
    ) -> "User":
        return cls(
            id=id,
            tenant_id=tenant_id,
            username=username,
            email=email,
            roles=roles or [],
            is_active=is_active,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "username": self.username,
            "email": self.email,
            "roles": self.roles,
            "is_active": self.is_active,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "User":
        return cls(
            id=data["id"],
            tenant_id=data["tenant_id"],
            username=data["username"],
            email=data["email"],
            roles=data.get("roles", []),
            is_active=data.get("is_active", True),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
        )


@dataclass
class Role:
    name: str
    permissions: list[str] = field(default_factory=list)
    description: str = ""

    # Predefined role names
    PROCUREMENT_REQUESTER = "procurement_requester"
    PROCUREMENT_MANAGER = "procurement_manager"
    FINANCE_APPROVER = "finance_approver"
    ADMIN = "admin"

    # Predefined permissions
    PERM_PROCUREMENT_CREATE = "procurement:create"
    PERM_PROCUREMENT_VIEW = "procurement:view"
    PERM_PROCUREMENT_APPROVE_PREFIX = "procurement:approve:"
    PERM_ADMIN_ALL = "admin:*"

    def has_permission(self, permission: str) -> bool:
        """Check if this role has a specific permission (supports wildcards)."""
        for perm in self.permissions:
            if perm == permission:
                return True
            if perm.endswith("*"):
                prefix = perm[:-1]
                if permission.startswith(prefix):
                    return True
            if perm.startswith(self.PERM_PROCUREMENT_APPROVE_PREFIX):
                try:
                    threshold = int(perm[len(self.PERM_PROCUREMENT_APPROVE_PREFIX):])
                    if permission.startswith(self.PERM_PROCUREMENT_APPROVE_PREFIX):
                        req_threshold = int(permission[len(self.PERM_PROCUREMENT_APPROVE_PREFIX):])
                        if req_threshold <= threshold:
                            return True
                except ValueError:
                    pass
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "permissions": self.permissions,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Role":
        return cls(
            name=data["name"],
            permissions=data.get("permissions", []),
            description=data.get("description", ""),
        )


# Predefined roles (defined after Role class to avoid circular reference)
PREDEFINED_ROLES: dict[str, Role] = {
    Role.PROCUREMENT_REQUESTER: Role(
        name=Role.PROCUREMENT_REQUESTER,
        permissions=[Role.PERM_PROCUREMENT_CREATE, Role.PERM_PROCUREMENT_VIEW],
        description="Can create procurement requests and view own requests",
    ),
    Role.PROCUREMENT_MANAGER: Role(
        name=Role.PROCUREMENT_MANAGER,
        permissions=[
            Role.PERM_PROCUREMENT_CREATE,
            Role.PERM_PROCUREMENT_VIEW,
            f"{Role.PERM_PROCUREMENT_APPROVE_PREFIX}10000",
        ],
        description="Can create, view, and approve procurement up to threshold",
    ),
    Role.FINANCE_APPROVER: Role(
        name=Role.FINANCE_APPROVER,
        permissions=[
            Role.PERM_PROCUREMENT_VIEW,
            f"{Role.PERM_PROCUREMENT_APPROVE_PREFIX}100000",
        ],
        description="Can view and approve procurement (finance-level thresholds)",
    ),
    Role.ADMIN: Role(
        name=Role.ADMIN,
        permissions=[Role.PERM_ADMIN_ALL],
        description="Full administrative access",
    ),
}

DEFAULT_ROLES = [
    Role.PROCUREMENT_REQUESTER,
    Role.PROCUREMENT_MANAGER,
    Role.FINANCE_APPROVER,
    Role.ADMIN,
]


def get_predefined_role(name: str) -> Role | None:
    return PREDEFINED_ROLES.get(name)


def all_predefined_roles() -> list[Role]:
    return list(PREDEFINED_ROLES.values())

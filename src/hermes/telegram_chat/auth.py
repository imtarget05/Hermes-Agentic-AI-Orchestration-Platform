"""RBAC-integrated auth for 1:1 chat.

Extends the simple allowlist with tenant-aware RBAC.
"""
from __future__ import annotations

from ..auth import RBACEngine, User

_rbac_engine: RBACEngine | None = None


def _get_rbac_engine() -> RBACEngine:
    global _rbac_engine
    if _rbac_engine is None:
        _rbac_engine = RBACEngine()
        _seed_default_rbac(_rbac_engine)
    return _rbac_engine


def _seed_default_rbac(engine: RBACEngine) -> None:
    from ..auth.models import Tenant
    tenant = Tenant.create("default", "Default Tenant")
    engine.add_tenant(tenant)
    engine.add_policy(engine.get_or_create_default_policy("default"))
    
    users = [
        User.create("user_1", "default", "alice", "alice@example.com", ["procurement_requester"]),
        User.create("user_2", "default", "bob", "bob@example.com", ["procurement_manager"]),
        User.create("user_3", "default", "carol", "carol@example.com", ["finance_approver"]),
        User.create("user_4", "default", "dave", "dave@example.com", ["admin"]),
    ]
    for user in users:
        engine.add_user(user)


def _norm_username(u: str | None) -> str:
    return (u or "").strip().lstrip("@").lower()


def is_allowed(user_id: int | str | None, username: str | None,
               allowed: list[str]) -> bool:
    """Legacy allowlist check — kept for backward compatibility."""
    if not allowed:
        return True
    ids: set[str] = set()
    names: set[str] = set()
    for raw in allowed:
        a = (raw or "").strip()
        if not a:
            continue
        if a.lstrip("-").isdigit():
            ids.add(a)
        else:
            names.add(_norm_username(a))
    if user_id is not None and str(user_id).strip() in ids:
        return True
    if username and _norm_username(username) in names:
        return True
    return False


def get_user_from_telegram(user_id: int | str | None, username: str | None) -> User | None:
    """Look up a user in the RBAC system from Telegram credentials."""
    engine = _get_rbac_engine()
    if username:
        user = engine.get_user_by_username("default", username)
        if user:
            return user
    if user_id is not None:
        user = engine.get_user(str(user_id))
        if user:
            return user
    return None


def check_permission(user: User | None, permission: str) -> bool:
    """Check if a user has a specific permission."""
    if user is None:
        return False
    engine = _get_rbac_engine()
    return engine.check_permission(user, permission)


def user_can_approve_amount(user: User | None, amount: float) -> tuple[bool, str]:
    """Check if a user can approve a request of the given amount."""
    if user is None:
        return False, "User not found"
    engine = _get_rbac_engine()
    return engine.user_can_approve(user, amount)


def allowed_from_string(raw: str) -> list[str]:
    return [u.strip() for u in (raw or "").split(",") if u.strip()]


def seed_default_users() -> None:
    """Seed default users for development/demo purposes."""
    _get_rbac_engine()  # This will initialize and seed

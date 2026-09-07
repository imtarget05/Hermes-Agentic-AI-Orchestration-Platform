"""RBAC enforcement engine."""
from __future__ import annotations

from .approval_policy import ApprovalPolicy
from .models import Role, Tenant, User, all_predefined_roles


class RBACEngine:
    """Role-Based Access Control engine with tenant isolation."""

    def __init__(
        self,
        tenants: dict[str, Tenant] | None = None,
        users: dict[str, User] | None = None,
        roles: dict[str, Role] | None = None,
        policies: dict[str, ApprovalPolicy] | None = None,
    ):
        self._tenants = tenants or {}
        self._users = users or {}
        self._roles = roles or {}
        self._policies = policies or {}

        for role in all_predefined_roles():
            self._roles[role.name] = role

    def add_tenant(self, tenant: Tenant) -> None:
        self._tenants[tenant.id] = tenant

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        return self._tenants.get(tenant_id)

    def add_user(self, user: User) -> None:
        self._users[user.id] = user

    def get_user(self, user_id: str) -> User | None:
        return self._users.get(user_id)

    def get_user_by_username(self, tenant_id: str, username: str) -> User | None:
        for user in self._users.values():
            if user.tenant_id == tenant_id and user.username == username:
                return user
        return None

    def add_role(self, role: Role) -> None:
        self._roles[role.name] = role

    def get_role(self, name: str) -> Role | None:
        return self._roles.get(name)

    def list_roles(self) -> list[Role]:
        """All roles known to the engine (predefined + tenant-added)."""
        return list(self._roles.values())

    def find_users_by_role(self, role_name: str) -> list[User]:
        """All active users holding exactly this role."""
        return [u for u in self._users.values()
                if u.is_active and role_name in u.roles]

    def add_policy(self, policy: ApprovalPolicy) -> None:
        self._policies[policy.tenant_id] = policy

    def get_policy(self, tenant_id: str) -> ApprovalPolicy | None:
        return self._policies.get(tenant_id)

    def get_or_create_default_policy(self, tenant_id: str) -> ApprovalPolicy:
        if tenant_id not in self._policies:
            self._policies[tenant_id] = ApprovalPolicy.default_for_tenant(tenant_id)
        return self._policies[tenant_id]

    def check_permission(self, user: User, permission: str) -> bool:
        """Check if a user has a specific permission."""
        if not user.is_active:
            return False

        for role_name in user.roles:
            role = self.get_role(role_name)
            if role and role.has_permission(permission):
                return True
        return False

    def get_user_roles(self, user_id: str, tenant_id: str) -> list[str]:
        """Get all role names for a user in a tenant."""
        user = self.get_user(user_id)
        if not user or user.tenant_id != tenant_id:
            return []
        return list(user.roles)

    def user_can_approve(self, user: User, amount: float, tenant_policy: ApprovalPolicy | None = None) -> tuple[bool, str]:
        """Check if a user can approve a request of a given amount."""
        if not user.is_active:
            return False, "User is inactive"

        if tenant_policy is None:
            tenant_policy = self.get_or_create_default_policy(user.tenant_id)

        threshold = tenant_policy.get_threshold_for_amount(amount)
        if threshold is None:
            return False, f"No approval threshold for amount {amount}"

        required_roles = set(threshold.required_roles)
        user_roles = set(user.roles)

        missing_roles = required_roles - user_roles
        if missing_roles:
            return False, f"Missing required roles: {', '.join(sorted(missing_roles))}"

        return True, "User can approve"

    def get_user_permissions(self, user: User) -> set[str]:
        """Get all permissions for a user (expanded)."""
        perms = set()
        for role_name in user.roles:
            role = self.get_role(role_name)
            if role:
                perms.update(role.permissions)
        return perms

    def user_has_role(self, user: User, role_name: str) -> bool:
        return role_name in user.roles

    def assign_role(self, user_id: str, role_name: str) -> bool:
        user = self.get_user(user_id)
        if not user:
            return False
        if role_name not in self._roles:
            return False
        if role_name not in user.roles:
            user.roles.append(role_name)
        return True

    def revoke_role(self, user_id: str, role_name: str) -> bool:
        user = self.get_user(user_id)
        if not user:
            return False
        if role_name in user.roles:
            user.roles.remove(role_name)
        return True

    def get_effective_policy(self, tenant_id: str) -> ApprovalPolicy:
        return self.get_or_create_default_policy(tenant_id)

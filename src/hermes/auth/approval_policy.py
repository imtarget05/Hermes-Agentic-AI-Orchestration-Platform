"""Approval policy with budget thresholds and separation of duties."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ApprovalThreshold:
    min_amount: float
    max_amount: float | None
    required_roles: list[str]

    def matches(self, amount: float) -> bool:
        if amount < self.min_amount:
            return False
        if self.max_amount is not None and amount > self.max_amount:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_amount": self.min_amount,
            "max_amount": self.max_amount,
            "required_roles": self.required_roles,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApprovalThreshold":
        return cls(
            min_amount=data["min_amount"],
            max_amount=data.get("max_amount"),
            required_roles=data.get("required_roles", []),
        )


def _default_thresholds() -> list[ApprovalThreshold]:
    return [
        ApprovalThreshold(
            min_amount=0,
            max_amount=999.99,
            required_roles=["procurement_manager"],
        ),
        ApprovalThreshold(
            min_amount=1000,
            max_amount=9999.99,
            required_roles=["procurement_manager", "finance_approver"],
        ),
        ApprovalThreshold(
            min_amount=10000,
            max_amount=None,
            required_roles=["procurement_manager", "finance_approver", "admin"],
        ),
    ]


@dataclass
class ApprovalPolicy:
    tenant_id: str
    thresholds: list[ApprovalThreshold] = field(default_factory=_default_thresholds)
    separation_of_duties: bool = True

    def get_threshold_for_amount(self, amount: float) -> ApprovalThreshold | None:
        for threshold in sorted(self.thresholds, key=lambda t: t.min_amount):
            if threshold.matches(amount):
                return threshold
        return None

    def evaluate_approval(
        self,
        request_amount: float,
        requester_id: str,
        approver_id: str,
        approver_roles: list[str],
    ) -> tuple[bool, str]:
        """Evaluate if an approval is allowed.
        
        Returns:
            (allowed: bool, reason: str)
        """
        threshold = self.get_threshold_for_amount(request_amount)
        if threshold is None:
            return False, f"No approval threshold defined for amount {request_amount}"

        required_roles = set(threshold.required_roles)
        approver_role_set = set(approver_roles)

        missing_roles = required_roles - approver_role_set
        if missing_roles:
            return False, f"Approver lacks required roles: {', '.join(sorted(missing_roles))}"

        if self.separation_of_duties and requester_id == approver_id:
            return False, "Separation of duties violation: requester cannot be the approver"

        return True, "Approval allowed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "thresholds": [t.to_dict() for t in self.thresholds],
            "separation_of_duties": self.separation_of_duties,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApprovalPolicy":
        return cls(
            tenant_id=data["tenant_id"],
            thresholds=[ApprovalThreshold.from_dict(t) for t in data.get("thresholds", [])],
            separation_of_duties=data.get("separation_of_duties", True),
        )

    @classmethod
    def default_for_tenant(cls, tenant_id: str) -> "ApprovalPolicy":
        return cls(tenant_id=tenant_id, thresholds=_default_thresholds())

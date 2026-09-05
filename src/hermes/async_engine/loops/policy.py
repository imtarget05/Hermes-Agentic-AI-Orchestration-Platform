"""Policy Engine — risk-based tool authorization.

Extends the existing permission model (ToolExecutor.can_call) with a risk
layer that decides ALLOW / DENY / REQUIRE_APPROVAL per tool call:

    Agent wants delete_data()  →  PolicyEngine  →  DENY
    Agent wants run_shell()     →  PolicyEngine  →  REQUIRE_APPROVAL  →  HITL
    Agent wants web_search()    →  PolicyEngine  →  ALLOW

Policy is a JSON file (version-controlled, runtime-loaded). HITL consumes
REQUIRE_APPROVAL decisions.
"""
from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

from ...tools import REGISTRY, ToolExecutor


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Decision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


DEFAULT_TOOL_RISK: dict[str, RiskLevel] = {
    "web_search": RiskLevel.LOW,
    "read_file": RiskLevel.LOW,
    "write_file": RiskLevel.MEDIUM,
    "run_shell": RiskLevel.HIGH,
}
DEFAULT_AGENT_CLEARANCE: dict[str, RiskLevel] = {
    "research": RiskLevel.LOW,
    "builder": RiskLevel.MEDIUM,
    "validator": RiskLevel.LOW,
    "general": RiskLevel.LOW,
}

POLICY_PATH_DEFAULT = "./hermes_policy.json"


class PolicyDecision:
    def __init__(self, decision: Decision, reason: str = "", risk: RiskLevel = RiskLevel.LOW):
        self.decision, self.reason, self.risk = decision, reason, risk


class PolicyEngine:
    def __init__(self, tool_risk: dict[str, str] | None = None,
                 agent_clearance: dict[str, str] | None = None,
                 high_risk_requires_approval: bool = True):
        self.tool_risk: dict[str, RiskLevel] = {}
        for k, v in {**DEFAULT_TOOL_RISK, **(tool_risk or {})}.items():
            self.tool_risk[k] = RiskLevel(v) if isinstance(v, str) else v
        self.agent_clearance: dict[str, RiskLevel] = {}
        for k, v in {**DEFAULT_AGENT_CLEARANCE, **(agent_clearance or {})}.items():
            self.agent_clearance[k] = RiskLevel(v) if isinstance(v, str) else v
        self.high_risk_requires_approval = high_risk_requires_approval

    def risk_for(self, tool_name: str) -> RiskLevel:
        return self.tool_risk.get(tool_name, RiskLevel.MEDIUM)

    def clearance_for(self, agent_role: str) -> RiskLevel:
        return self.agent_clearance.get(agent_role, RiskLevel.LOW)

    def evaluate(self, agent_role: str, tool_name: str, args: dict | None = None) -> PolicyDecision:
        if tool_name not in REGISTRY:
            return PolicyDecision(Decision.DENY, f"unknown tool: {tool_name}", RiskLevel.HIGH)
        risk = self.risk_for(tool_name)
        clearance = self.clearance_for(agent_role)
        risk_rank = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
        if risk_rank[risk] > risk_rank[clearance]:
            return PolicyDecision(Decision.DENY,
                                  f"agent '{agent_role}' (clearance {clearance.value}) "
                                  f"cannot call '{tool_name}' (risk {risk.value})", risk)
        if risk == RiskLevel.HIGH and self.high_risk_requires_approval:
            return PolicyDecision(Decision.REQUIRE_APPROVAL,
                                  f"'{tool_name}' is HIGH risk — requires human approval", risk)
        if risk == RiskLevel.MEDIUM:
            return PolicyDecision(Decision.ALLOW,
                                  f"'{tool_name}' is MEDIUM risk — allowed with clearance", risk)
        return PolicyDecision(Decision.ALLOW, f"'{tool_name}' is LOW risk — allowed", risk)

    @classmethod
    def from_file(cls, path: str = POLICY_PATH_DEFAULT) -> "PolicyEngine":
        p = Path(path)
        if not p.exists():
            return cls()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return cls(tool_risk=data.get("tool_risk", {}),
                       agent_clearance=data.get("agent_clearance", {}),
                       high_risk_requires_approval=data.get("high_risk_requires_approval", True))
        except Exception:
            return cls()

    def save(self, path: str = POLICY_PATH_DEFAULT) -> None:
        data = {"tool_risk": {k: v.value for k, v in self.tool_risk.items()},
                "agent_clearance": {k: v.value for k, v in self.agent_clearance.items()},
                "high_risk_requires_approval": self.high_risk_requires_approval}
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")


class PolicyRequiredError(Exception):
    """Raised when a tool call needs human approval (feeds into HITL)."""
    def __init__(self, tool_name: str, agent_role: str, decision: PolicyDecision):
        self.tool_name, self.agent_role, self.decision = tool_name, agent_role, decision
        super().__init__(decision.reason)


class PolicyDeniedError(Exception):
    def __init__(self, tool_name: str, agent_role: str, decision: PolicyDecision):
        self.tool_name, self.agent_role, self.decision = tool_name, agent_role, decision
        super().__init__(decision.reason)


def policy_aware_call(executor: ToolExecutor, policy: PolicyEngine,
                      agent_role: str, tool_name: str, **kwargs) -> str:
    """Run a tool call through both permission check and policy engine.
    Raises PolicyDeniedError / PolicyRequiredError instead of proceeding."""
    dec = policy.evaluate(agent_role, tool_name, kwargs)
    if dec.decision == Decision.DENY:
        raise PolicyDeniedError(tool_name, agent_role, dec)
    if dec.decision == Decision.REQUIRE_APPROVAL:
        raise PolicyRequiredError(tool_name, agent_role, dec)
    return executor.call(tool_name, **kwargs)
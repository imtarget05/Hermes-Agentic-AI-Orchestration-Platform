"""Routing plan data models for Router-First Architecture (P0-1).

Deterministic intent classification → RoutingPlan with required agents,
estimated complexity, and latency budget. No LLM needed for routing.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Intent(str, Enum):
    """Classified intent categories."""
    SIMPLE_TASK = "simple_task"
    COMPARE_TASK = "compare_task"
    PROCUREMENT_DECISION = "procurement_decision"
    HELP = "help"
    PRICE_QUESTION = "price_question"
    INBOX = "inbox"
    APPROVAL = "approval"
    CHITCHAT = "chitchat"


class Complexity(str, Enum):
    """Estimated complexity tiers."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RoutingPlan(BaseModel):
    """Complete routing decision for a user request."""
    intent: Intent
    required_agents: list[str] = Field(default_factory=list)
    estimated_complexity: Complexity = Complexity.LOW
    estimated_latency_ms: int = 0
    quote_count: int = 0
    has_spec_details: bool = False
    reasoning: str = ""

    def model_dump_json(self, **kwargs) -> str:
        return super().model_dump_json(**kwargs)


# Agent names as used in the procurement DAG
AGENT_PRICE = "price"
AGENT_VENDOR = "vendor"
AGENT_CONTRACT = "contract"
AGENT_SPEC = "spec"
AGENT_ANALYSIS = "analysis"
AGENT_VERIFICATION = "verification"

# Full procurement DAG (6 agents in topological order)
FULL_PROCUREMENT_AGENTS = [
    AGENT_PRICE,
    AGENT_VENDOR,
    AGENT_CONTRACT,
    AGENT_SPEC,
    AGENT_ANALYSIS,
    AGENT_VERIFICATION,
]

# Compare task: 3 agents (price + vendor + spec)
COMPARE_AGENTS = [
    AGENT_PRICE,
    AGENT_VENDOR,
    AGENT_SPEC,
]

# Simple task: single agent (context-dependent)
SIMPLE_AGENTS = {
    "quote_validity": [AGENT_CONTRACT],
    "vendor_check": [AGENT_VENDOR],
    "price_check": [AGENT_PRICE],
    "spec_check": [AGENT_SPEC],
}

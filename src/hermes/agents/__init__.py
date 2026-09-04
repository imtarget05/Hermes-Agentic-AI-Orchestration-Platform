"""Specialized agents with isolated permissions (§4 of spec).

Each agent: system prompt + allowed tool permissions + run() that
reasons → selects tool → executes → observes. LLM hook injectable;
falls back to deterministic stub so tests run without API key.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from ..tools import ToolExecutor, REGISTRY


@dataclass
class BaseAgent:
    name: str
    role: str
    system_prompt: str
    allowed_permissions: set[str] = field(default_factory=lambda: {"general"})
    llm: object = None  # callable(prompt)->str | None = stub

    def executor(self, max_retries: int = 3) -> ToolExecutor:
        return ToolExecutor(set(self.allowed_permissions), max_retries)

    def think(self, task_text: str, context: str = "") -> str:
        if self.llm:
            try:
                return str(self.llm(f"{self.system_prompt}\nTask: {task_text}\nContext: {context}"))
            except Exception as e:
                return f"[{self.name} llm-fallback: {e}] {context}"
        return f"[{self.name}:{self.role}] processed: {task_text[:200]} | ctx: {context[:200]}"

    def run(self, task_text: str, context: str = "", tool_calls: list[dict] | None = None,
            max_retries: int = 3) -> str:
        ex = self.executor(max_retries)
        ctx = context
        for tc in tool_calls or []:
            try:
                out = ex.call(tc["tool"], **tc.get("args", {}))
                ctx += f"\n[tool:{tc['tool']}] {out[:500]}"
            except Exception as e:
                ctx += f"\n[tool:{tc['tool']} ERROR] {e}"
        return self.think(task_text, ctx)


RESEARCH = BaseAgent(
    name="research", role="Research",
    system_prompt="You are Research agent. Gather facts via web_search/read_file only. Never write files.",
    allowed_permissions={"general", "research"},
)
BUILDER = BaseAgent(
    name="builder", role="Builder",
    system_prompt="You are Builder agent. Implement via write_file/run_shell/read_file. Keep outputs small and sandboxed.",
    allowed_permissions={"general", "build"},
)
CRITIC = BaseAgent(
    name="validator", role="Validator/Critic",
    system_prompt="You are Critic agent. Validate correctness, list issues, demand revision if needed.",
    allowed_permissions={"general", "research"},
)

AGENTS: dict[str, BaseAgent] = {"research": RESEARCH, "builder": BUILDER, "validator": CRITIC}


def configure_agents_llm(llm) -> None:
    """Inject shared LLM callable into all agents (None = stub mode)."""
    for a in AGENTS.values():
        a.llm = llm

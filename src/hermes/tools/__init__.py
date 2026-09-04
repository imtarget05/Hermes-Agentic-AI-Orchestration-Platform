"""Tool registry + guarded executor (§5 of spec).

Key question answered here: under what conditions may an agent call
a tool, and what happens on failure?
- permission: each tool declares required permission; each agent
  declares allowed permissions → denied otherwise.
- retryable: only RetryableToolError triggers retry path; FatalToolError
  goes straight to failure. Denylist guards injection.
"""
from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

DENY_PATTERN = re.compile(r"(rm\s+-rf\s+/( |$)|:\(\)\s*\{|:;\s*\}|\bshutdown\b|\breboot\b)", re.IGNORECASE)


class RetryableToolError(Exception):
    pass


class FatalToolError(Exception):
    pass


@dataclass
class ToolSpec:
    name: str
    fn: Callable[..., str]
    permission: str = "general"
    retryable: bool = True
    timeout: int = 30
    description: str = ""


REGISTRY: dict[str, ToolSpec] = {}


def register_tool(name: str, permission: str = "general", retryable: bool = True,
                  timeout: int = 30, description: str = ""):
    def deco(fn: Callable[..., str]):
        REGISTRY[name] = ToolSpec(name, fn, permission, retryable, timeout, description)
        return fn
    return deco


def guard_input(text: str) -> None:
    if DENY_PATTERN.search(text or ""):
        raise FatalToolError("Input blocked by injection/denylist guard")


@dataclass
class ToolExecutor:
    allowed_permissions: set[str] = field(default_factory=lambda: {"general"})
    max_retries: int = 3
    log: list[dict] = field(default_factory=list)

    def can_call(self, name: str) -> bool:
        spec = REGISTRY.get(name)
        return bool(spec and spec.permission in self.allowed_permissions)

    def call(self, name: str, **kwargs) -> str:
        spec = REGISTRY.get(name)
        if not spec:
            raise FatalToolError(f"Unknown tool: {name}")
        if spec.permission not in self.allowed_permissions:
            raise FatalToolError(f"Permission denied: agent lacks '{spec.permission}' for tool '{name}'")
        raw = " ".join(str(v) for v in kwargs.values())
        guard_input(raw)
        attempts = 0
        while True:
            try:
                out = spec.fn(**kwargs)
                self.log.append({"tool": name, "ok": True, "attempts": attempts + 1})
                return out
            except FatalToolError:
                self.log.append({"tool": name, "ok": False, "fatal": True})
                raise
            except Exception as e:
                attempts += 1
                if not spec.retryable or attempts > self.max_retries:
                    self.log.append({"tool": name, "ok": False, "attempts": attempts})
                    raise RetryableToolError(f"Tool '{name}' failed after {attempts} attempts: {e}") from e
                time.sleep(min(2 ** attempts * 0.05, 1.0))


# ---- MVP tools ----

@register_tool("web_search", permission="research", description="Mockable web search")
def web_search(query: str, mock: str = "") -> str:
    if mock:
        return mock
    return f"[search stub] results for: {query} (plug real API later)"


@register_tool("read_file", permission="general", retryable=False, description="Read file inside sandbox")
def read_file(path: str, sandbox: str = "./sandbox") -> str:
    guard_input(path)
    base = Path(sandbox).resolve()
    target = (base / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    if base not in target.parents and target != base:
        # allow only sandbox subtree
        if not str(target).startswith(str(base)):
            raise FatalToolError("read_file: path outside sandbox")
    if not target.exists():
        raise FatalToolError(f"read_file: not found: {path}")
    return target.read_text()[:8000]


@register_tool("write_file", permission="build", description="Write file inside sandbox")
def write_file(path: str, content: str, sandbox: str = "./sandbox") -> str:
    guard_input(path + content[:2000])
    base = Path(sandbox).resolve()
    base.mkdir(parents=True, exist_ok=True)
    target = (base / path).resolve()
    if not str(target).startswith(str(base)):
        raise FatalToolError("write_file: path outside sandbox")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content[:50000])
    return f"wrote {len(content)} chars to {path}"


@register_tool("run_shell", permission="build", timeout=15, description="Allowlisted shell")
def run_shell(cmd: str) -> str:
    guard_input(cmd)
    allowed = ("echo ", "ls ", "pwd", "python3 --version", "cat ")
    if not any(cmd.strip().startswith(a) for a in allowed):
        raise FatalToolError(f"run_shell: command not allowlisted: {cmd!r}")
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
        return (r.stdout + r.stderr)[:4000] or "(no output)"
    except subprocess.TimeoutExpired as e:
        raise RetryableToolError(f"run_shell timeout: {e}") from e

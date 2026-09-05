"""Rule-based router + LLM RouterAgent hook (§12 Phase 4).

Rule-based is default (no API key needed). RouterAgent upgrades to
LLM classification when a `classify` callable is injected.
"""
from __future__ import annotations

from .registry import Route, RoutingRegistry


class RouterAgent:
    def __init__(self, registry: RoutingRegistry, classify=None):
        self.registry = registry
        self._classify = classify  # fn(text)->project | None

    def route(self, text: str, project_hint: str = "") -> tuple[str, Route]:
        if project_hint and project_hint in self.registry.routes:
            return project_hint, self.registry.resolve(project_hint)
        if self._classify:
            try:
                guess = (self._classify(text) or "").strip().lower()
                if guess in self.registry.routes:
                    return guess, self.registry.resolve(guess)
            except Exception:
                pass
        t = text.lower()
        for proj in self.registry.projects():
            if proj != "default" and proj in t:
                return proj, self.registry.resolve(proj)
        return "default", self.registry.resolve("default")

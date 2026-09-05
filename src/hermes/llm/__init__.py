"""LLM factory. Default provider: cloudflare (Workers AI)."""
from __future__ import annotations

from .cloudflare import CloudflareError, CloudflareLLM


def build_llm(provider: str = "cloudflare", model: str = "",
              account_id: str = "", api_token: str = "",
              timeout: int = 60, system: str = ""):
    """Return callable(prompt)->str or None if credentials missing.

    - cloudflare: requires account_id + api_token (Workers AI direct).
    - stub/None: returns None → agents fall back to deterministic stub (tests/local).
    """
    p = (provider or "").lower()
    if p == "cloudflare":
        mdl = model or "@cf/meta/llama-3.1-8b-instruct"
        if not account_id or not api_token:
            return None
        return CloudflareLLM(account_id, api_token, mdl, timeout, system)
    if p in ("none", "stub", ""):
        return None
    # openai/anthropic hooks can be added later; unknown → stub-safe None
    return None


def build_router_classifier(llm, projects: list[str]):
    """Return classify(text)->project for RouterAgent (None if no LLM).

    Prompts the LLM to pick exactly one project name; falls back to
    rule-based routing on any failure/unknown output.
    """
    if llm is None:
        return None
    valid = set(projects)

    def classify(text: str) -> str | None:
        try:
            out = llm.complete(
                f"Classify this task into exactly one project from {sorted(valid)}. "
                f"Reply with ONLY the project name, nothing else.\nTask: {text}"
            )
            guess = out.strip().lower().split()[0].strip(".,:\"'")
            return guess if guess in valid else None
        except Exception:
            return None

    return classify


__all__ = ["CloudflareError", "CloudflareLLM", "build_llm", "build_router_classifier"]

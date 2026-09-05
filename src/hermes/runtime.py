"""Runtime bootstrap shared by CLI gateway and HTTP API (deployment entrypoint)."""
from __future__ import annotations

from .agents import configure_agents_llm
from .config import settings
from .llm import build_llm, build_router_classifier
from .messaging import SafeNotifier, build_notifier
from .orchestrator import orchestrate
from .router import RouterAgent, RoutingRegistry
from .tasks import Task, TaskStore


class HermesRuntime:
    """Wires registry + LLM + router + store + notifier once, runs tasks."""

    def __init__(self) -> None:
        self.registry = RoutingRegistry(settings.hermes_routing_path)
        self.llm = build_llm(
            settings.llm_provider,
            settings.cloudflare_model or settings.llm_model,
            settings.cloudflare_account_id,
            settings.cloudflare_api_token,
            settings.cloudflare_timeout,
        )
        configure_agents_llm(self.llm)
        self.router = RouterAgent(self.registry, classify=build_router_classifier(self.llm, self.registry.projects()))
        self.store = TaskStore(settings.hermes_db_path, dsn=settings.hermes_database_url or None)
        self.notifier = SafeNotifier(build_notifier(settings.telegram_bot_token, self.registry))

    @property
    def llm_mode(self) -> str:
        return f"cloudflare {self.settings_model}" if self.llm else "stub"

    @property
    def settings_model(self) -> str:
        return self.settings.cloudflare_model or self.settings.llm_model

    @property
    def settings(self):
        from .config import settings
        return settings

    @property
    def notifier_mode(self) -> str:
        return "telegram" if self.settings.telegram_bot_token else "mock"

    def run_task(self, text: str, project: str = "", strategy: str = "fanout", user: str = "local") -> Task:
        """Full pipeline: allowlist → route → create → orchestrate. Raises on failure."""
        if self.settings.allowed_users and user not in self.settings.allowed_users:
            raise PermissionError(f"User '{user}' not in allowlist")
        if strategy not in ("fanout", "pipeline", "critic"):
            raise ValueError(f"unknown strategy: {strategy}")
        proj, route = self.router.route(text, project)
        task = self.store.create(Task(text=text, project=proj, strategy=strategy, max_retries=self.settings.max_retries))
        orchestrate(task.id, self.store, self.notifier)
        return self.store.get(task.id)

"""Gateway process: receive task → route → orchestrate → report (§8)."""
from __future__ import annotations

import click

from ..agents import configure_agents_llm
from ..config import settings
from ..llm import build_llm, build_router_classifier
from ..messaging import SafeNotifier, build_notifier
from ..orchestrator import orchestrate
from ..router import RouterAgent, RoutingRegistry
from ..tasks import Task, TaskStore


@click.command()
@click.option("--once", is_flag=True, help="Run a single task then exit")
@click.option("--text", default="", help="Task text (else read stdin)")
@click.option("--project", default="", help="Project hint")
@click.option("--strategy", default="fanout", type=click.Choice(["fanout", "pipeline", "critic"]))
@click.option("--user", default="local", help="Requesting user (checked vs allowlist if configured)")
def main(once: bool, text: str, project: str, strategy: str, user: str):
    registry = RoutingRegistry(settings.hermes_routing_path)
    llm = build_llm(
        settings.llm_provider,
        settings.cloudflare_model or settings.llm_model,
        settings.cloudflare_account_id,
        settings.cloudflare_api_token,
        settings.cloudflare_timeout,
    )
    configure_agents_llm(llm)
    if llm:
        click.echo(f"llm: cloudflare {settings.cloudflare_model or settings.llm_model}")
    else:
        click.echo("llm: stub (set CLOUDFLARE_ACCOUNT_ID + CLOUDFLARE_API_TOKEN for Workers AI)")
    router = RouterAgent(registry, classify=build_router_classifier(llm, registry.projects()))
    if router._classify:
        click.echo("router: LLM classifier on")
    else:
        click.echo("router: rule-based")
    store = TaskStore(settings.hermes_db_path)
    notifier = SafeNotifier(build_notifier(settings.telegram_bot_token, registry))
    click.echo(f"notifier: {'telegram' if settings.telegram_bot_token else 'mock'}")

    if settings.allowed_users and user not in settings.allowed_users:
        raise click.ClickException(f"User '{user}' not in allowlist")

    if not text:
        import sys
        text = sys.stdin.read().strip() if not sys.stdin.isatty() else ""
    if not text:
        raise click.ClickException("No task text (--text or stdin)")

    proj, route = router.route(text, project)
    click.echo(f"routed → project={proj} channel={route.channel} thread={route.thread_id}")

    task = store.create(Task(text=text, project=proj, strategy=strategy, max_retries=settings.max_retries))
    click.echo(f"task {task.id} queued")
    final = orchestrate(task.id, store, notifier)
    click.echo(final)
    click.echo(f"\nstate: {store.export_json(task.id)[:500]}...")


if __name__ == "__main__":
    main()

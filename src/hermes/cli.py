"""Unified Hermes CLI - exposes all domains via subcommand groups.

Domains:
  procurement   Enterprise procurement case (DAG)
  scraper       Web/PDF scraping -> ingestion -> RAG storage
  knowledge     Team knowledge base + second brain (ingest + query)
  advisor       Advisory council (parallel personas -> synthesized report)
  competitor    Competitive intelligence (watch -> collect -> weekly brief)
  ops           Business operations hub (CRM/invoicing/calendar/inbox)
  report        Multi-type PDF report generation
  ingestion     Document ingestion pipeline
  router        Intent classification + routing
  harness       Guardrails, eval, observability metrics
  async         Async engine (smoke test, load test, workflow, etc.)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import click


@click.group()
@click.version_option(package_name="hermes-agentic-platform")
def cli():
    """Hermes - Agentic AI Orchestration Platform."""


# --------------------------------------------------------------------------- #
# procurement
# --------------------------------------------------------------------------- #
@cli.group()
def procurement():
    """Enterprise procurement case (DAG: price//vendor//contract//spec -> analysis -> verification)."""


@procurement.command("run")
@click.argument("request")
@click.option("--quotes", type=click.Path(exists=True), help="JSON file with vendor quotes")
@click.option("--required-spec", default="", help="Required spec string")
@click.option("--workers", default=4, show_default=True, help="Number of parallel workers")
@click.option("--auto-approve", is_flag=True, help="Auto-approve the purchase (HITL)")
@click.option("--db-path", default="", help="SQLite DB path (default: temp)")
def procurement_run(request, quotes, required_spec, workers, auto_approve, db_path):
    """Run a full procurement case from request text to recommendation."""
    from hermes.orchestrator import orchestrate
    from hermes.tasks import TaskStore
    from hermes.tasks.store import init_db

    if auto_approve:
        os.environ["HERMES_HITL_AUTO_APPROVE"] = "1"

    db = db_path or tempfile.mktemp(suffix=".db")
    store = TaskStore(db_path=db)
    init_db(store.db_path)

    quote_data = []
    if quotes:
        with open(quotes) as f:
            quote_data = json.load(f)

    task_id = store.create_task(
        text=request,
        project="procurement",
        agents=["price", "vendor", "contract", "spec", "analysis", "verification"],
    )

    click.echo(f"Task created: {task_id}")
    click.echo(f"Running procurement DAG ({workers} workers)...")

    try:
        result = orchestrate(task_id, store, quotes=quote_data, required_spec=required_spec, workers=workers)
        click.echo("\n=== RESULT ===")
        click.echo(result)
    except Exception as e:
        click.echo(f"FAILED: {e}", err=True)
        raise SystemExit(1)


@procurement.command("demo")
@click.option("--workers", default=4, show_default=True)
def procurement_demo(workers):
    """Run procurement with built-in DEMO quotes (Dell/Lenovo/HP)."""
    from hermes.procurement.pipeline import run_procurement_case
    from hermes.runtime import default_demo_quotes

    quotes = default_demo_quotes()
    click.echo(f"Running procurement DEMO with {len(quotes)} vendors...")

    agg = run_procurement_case(
        "Enterprise procurement: buy 500 laptops. Budget $1200/unit max.",
        quotes=quotes,
        required_spec="1080p, 15in, IPS, 16GB RAM",
        workers=workers,
    )
    click.echo(json.dumps(agg, indent=2, default=str))


# --------------------------------------------------------------------------- #
# scraper
# --------------------------------------------------------------------------- #
@cli.group()
def scraper():
    """Web/PDF scraping -> ingestion -> RAG storage."""


@scraper.command("run")
@click.option("--keys", multiple=True, required=True, help="URLs or file keys to scrape (repeatable)")
@click.option("--source", type=click.Choice(["web", "pdf", "vendor_catalog"]), default="web", show_default=True)
@click.option("--task-id", default="", help="Task ID (default: auto)")
@click.option("--rag-path", default="", help="RAG index path (default: HERMES_RAG_INDEX env)")
@click.option("--ttl-days", default=30, show_default=True, help="Chunk TTL in days")
@click.option("--rate-limit", default=1.0, show_default=True, help="Max requests/sec/domain")
def scraper_run(keys, source, task_id, rag_path, ttl_days, rate_limit):
    """Run a scrape task against the given URLs/keys."""
    from hermes.scraper.contract import ScrapeSource, ScrapeTask
    from hermes.scraper.db import ScrapedDocStore
    from hermes.scraper.pipeline import run_scrape
    from hermes.scraper.policy import RateLimiter, ScrapePolicy

    source_map = {"web": ScrapeSource.WEB, "pdf": ScrapeSource.PDF, "vendor_catalog": ScrapeSource.VENDOR_CATALOG}
    task = ScrapeTask(task_id=task_id or f"scrape-{os.getpid()}", source_type=source_map[source], keys=list(keys))
    policy = ScrapePolicy(ttl_days=ttl_days, rate_limit_per_domain=rate_limit)
    store = ScrapedDocStore(rag_path=rag_path, ttl_days=ttl_days)

    click.echo(f"Scraping {len(keys)} source(s) ({source})...")
    result = run_scrape(task, store=store, policy=policy, rate_limiter=RateLimiter(rate_per_sec=rate_limit))
    click.echo(json.dumps(result.model_dump() if hasattr(result, "model_dump") else vars(result), indent=2, default=str))


@scraper.command("cleanup")
@click.option("--rag-path", default="", help="RAG index path (default: HERMES_RAG_INDEX env)")
@click.option("--ttl-days", default=30, show_default=True)
def scraper_cleanup(rag_path, ttl_days):
    """Evict stale chunks from the RAG index."""

    rag = rag_path or os.environ.get("HERMES_RAG_INDEX", "")
    if not rag:
        click.echo("--rag-path or HERMES_RAG_INDEX required", err=True)

# --------------------------------------------------------------------------- #
# knowledge
# --------------------------------------------------------------------------- #
@cli.group()
def knowledge():
    """Team knowledge base + second brain (ingest + query)."""


@knowledge.command("ingest")
@click.argument("paths", nargs=-1, required=True)
@click.option("--scope", type=click.Choice(["team", "personal"]), default="team", show_default=True)
@click.option("--tenant-id", default="default", show_default=True)
@click.option("--user-id", default="", help="Owner user_id (required for personal scope)")
@click.option("--category", default="other", help="Document category")
@click.option("--title", default="", help="Document title")
def knowledge_ingest(paths, scope, tenant_id, user_id, category, title):
    """Ingest documents or directories into the knowledge base."""
    from hermes.knowledge import KnowledgeService, extract_text_from_file

    db = os.environ.get("HERMES_KNOWLEDGE_DB", tempfile.mktemp(suffix=".db"))
    svc = KnowledgeService(db_path=db)

    for path in paths:
        if os.path.isdir(path):
            for root, _, files in os.walk(path):
                for fn in files:
                    fp = os.path.join(root, fn)
                    try:
                        text = extract_text_from_file(fp)
                        if not text.strip():
                            click.echo(f"  skip {fp}: no extractable text", err=True)
                            continue
                        doc_id = svc.ingest(content=text, title=fn, scope=scope,
                                            tenant_id=tenant_id, user_id=user_id,
                                            category=category, source_uri=fp)
                        click.echo(f"  ingested: {fp} -> {doc_id}")
                    except Exception as e:
                        click.echo(f"  skip {fp}: {e}", err=True)
        else:
            try:
                text = extract_text_from_file(path)
                if not text.strip():
                    click.echo(f"  skip {path}: no extractable text", err=True)
                    continue
                doc_id = svc.ingest(content=text, title=title or os.path.basename(path), scope=scope,
                                    tenant_id=tenant_id, user_id=user_id,
                                    category=category, source_uri=path)
                click.echo(f"  ingested: {path} -> {doc_id}")
            except Exception as e:
                click.echo(f"  skip {path}: {e}", err=True)


@knowledge.command("query")
@click.argument("query_text")
@click.option("--scope", type=click.Choice(["team", "personal"]), default="team", show_default=True)
@click.option("--tenant-id", default="default", show_default=True)
@click.option("--user-id", default="", help="Owner user_id (for personal scope)")
@click.option("--limit", default=5, show_default=True, help="Max results")
def knowledge_query(query_text, scope, tenant_id, user_id, limit):
    """Query the knowledge base."""
    from hermes.knowledge import KnowledgeService

    db = os.environ.get("HERMES_KNOWLEDGE_DB", tempfile.mktemp(suffix=".db"))
    svc = KnowledgeService(db_path=db)
    answer = svc.query(text=query_text, scope=scope, tenant_id=tenant_id, user_id=user_id, limit=limit)
    click.echo(json.dumps(answer.model_dump(), indent=2, default=str))


# --------------------------------------------------------------------------- #
# advisor
# --------------------------------------------------------------------------- #
@cli.group()
def advisor():
    """Advisory council (parallel personas -> synthesized report)."""


@advisor.command("ask")
@click.argument("question")
@click.option("--personas", default="", help="Comma-separated persona names (default: all)")
@click.option("--context", default="", help="Internal context for the advisors")
@click.option("--max-workers", default=4, show_default=True)
def advisor_ask(question, personas, context, max_workers):
    """Ask the advisory council a question."""
    from hermes.advisor import ask_council

    persona_list = [p.strip() for p in personas.split(",") if p.strip()] or None
    council_report = ask_council(question, personas=persona_list, context=context, max_workers=max_workers)
    click.echo(json.dumps(council_report.model_dump(), indent=2, default=str))


# --------------------------------------------------------------------------- #
# competitor
# --------------------------------------------------------------------------- #
@cli.group()
def competitor():
    """Competitive intelligence (watch -> collect -> weekly brief)."""


@competitor.command("watch")
@click.option("--competitor", required=True, help="Competitor name")
@click.option("--urls", multiple=True, help="URLs to monitor (repeatable)")
@click.option("--feeds", multiple=True, help="RSS/atom feed URLs (repeatable)")
def competitor_watch(competitor, urls, feeds):
    """Add a competitor to the watch list."""
    from hermes.competitor import CompetitorTarget

    target = CompetitorTarget(competitor=competitor, urls=list(urls), feeds=list(feeds))
    watch_file = os.path.join(tempfile.gettempdir(), "hermes_competitor_watch.json")
    watches = []
    if os.path.exists(watch_file):
        with open(watch_file) as f:
            watches = json.load(f)
    watches.append(target.model_dump())
    with open(watch_file, "w") as f:
        json.dump(watches, f, indent=2, default=str)
    click.echo(f"Watching {competitor} ({len(urls)} URLs, {len(feeds)} feeds)")
    click.echo(f"Watch list: {watch_file}")


@competitor.command("brief")
def competitor_brief():
    """Generate a weekly competitive intelligence brief."""

# --------------------------------------------------------------------------- #
# ops
# --------------------------------------------------------------------------- #
@cli.group("ops")
def ops_cmd():
    """Business operations hub (CRM/invoicing/calendar/inbox attention)."""


@ops_cmd.command("sources")
def ops_sources():
    """List configured ops sources."""
    from hermes.ops import OpsHub

    hub = OpsHub()
    sources = hub.sources_json(tenant_id="default")
    click.echo(json.dumps(sources, indent=2, default=str))


@ops_cmd.command("add-source")
@click.option("--kind", required=True, help="Source kind (crm, invoicing, calendar, inbox)")
@click.option("--name", default="", help="Source name")
@click.option("--tenant-id", default="default", show_default=True)
@click.option("--enabled/--disabled", default=True)
def ops_add_source(kind, name, tenant_id, enabled):
    """Add an ops source."""
    from hermes.ops import OpsHub

    hub = OpsHub()
    src = hub.add_source(kind=kind, tenant_id=tenant_id, name=name, enabled=enabled)
    click.echo(json.dumps(src.model_dump(), indent=2, default=str))


@ops_cmd.command("attention")
def ops_attention():
    """Collect attention items across all ops sources."""
    from hermes.ops import OpsHub

    hub = OpsHub()
    ops_report = hub.collect_attention(tenant_id="default")
    click.echo(json.dumps(ops_report.model_dump(), indent=2, default=str))


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
@cli.group()
def report():
    """Multi-type PDF report generation."""


@report.command("generate")
@click.argument("report_type", type=click.Choice(["procurement", "financial", "maintenance", "research", "investigation", "workflow"]))
@click.option("--data", default="{}", help="JSON string of report data")
@click.option("--data-file", type=click.Path(exists=True), help="JSON file with report data (overrides --data)")
@click.option("--output", default="", help="Output PDF path (default: stdout)")
@click.option("--workflow-id", default="", help="Optional workflow ID")
@click.option("--task-id", default="", help="Optional task ID")
def report_generate(report_type, data, data_file, output, workflow_id, task_id):
    """Generate a PDF report of the given type."""
    from hermes.report import generate_report

    if data_file:
        with open(data_file) as f:
            data = f.read()
    data_dict = json.loads(data)

    pdf_bytes = generate_report(report_type=report_type, data=data_dict, workflow_id=workflow_id, task_id=task_id)

    if output:
        with open(output, "wb") as f:
            f.write(pdf_bytes)
        click.echo(f"PDF written to {output} ({len(pdf_bytes)} bytes)")
    else:
        sys.stdout.buffer.write(pdf_bytes)


# --------------------------------------------------------------------------- #
# ingestion
# --------------------------------------------------------------------------- #
@cli.group()
def ingestion():
    """Document ingestion pipeline (extract -> normalize -> dedupe -> chunk)."""


@ingestion.command("process")
@click.argument("path", type=click.Path(exists=True))
@click.option("--max-chunk-words", default=200, show_default=True)
@click.option("--min-content-chars", default=20, show_default=True)
def ingestion_process(path, max_chunk_words, min_content_chars):
    """Process a document through the ingestion pipeline."""
    from hermes.ingestion.model import RawSource
    from hermes.ingestion.pipeline import IngestionPipeline

    raw = RawSource(source_uri=path, doc_id=path)
    pipe = IngestionPipeline(max_chunk_words=max_chunk_words, min_content_chars=min_content_chars)
    doc = pipe.process(raw)

    click.echo(f"Source: {path}")
    click.echo(f"Blocks (canonical): {doc.canonical.block_count()}")
    click.echo(f"Blocks (deduped): {doc.deduped.block_count()}")
    click.echo(f"Chunks: {len(doc.chunks)}")
    click.echo(f"Diagnostics: {doc.diagnostics}")
    click.echo("\n--- Chunks ---")
    for i, chunk in enumerate(doc.chunks, 1):
        click.echo(f"[{i}] {chunk[:200]}..." if len(chunk) > 200 else f"[{i}] {chunk}")


# --------------------------------------------------------------------------- #
# router
# --------------------------------------------------------------------------- #
@cli.group()
def router():
    """Intent classification + routing."""


@router.command("classify")
@click.argument("text")
@click.option("--top-k", default=3, show_default=True)
def router_classify(text, top_k):
    """Classify intent and show routing for a request."""
    from hermes.config import settings
    from hermes.llm import build_llm, build_router_classifier
    from hermes.router import RoutingRegistry

    registry = RoutingRegistry(settings.hermes_routing_path)
    llm = build_llm(
        settings.llm_provider,
        settings.llm_model,
        settings.cloudflare_account_id,
        settings.cloudflare_api_token,
        settings.cloudflare_timeout,
    )
    classifier = build_router_classifier(llm, registry.projects())
    result = classifier.classify(text, top_k=top_k)
    click.echo(json.dumps(result, indent=2, default=str))


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #
@cli.group()
def harness():
    """Guardrails, eval, observability metrics."""


@harness.command("metrics")
def harness_metrics():
    """Show harness evaluation metrics."""
    from hermes.harness import HarnessEvaluator

    ev = HarnessEvaluator()
    metrics = [m.model_dump() for m in ev.compute_metrics()]
    click.echo(json.dumps(metrics, indent=2, default=str))


@harness.command("check-output")
@click.argument("output_text")
@click.option("--domain", default="procurement", show_default=True, help="Domain for rule selection")
def harness_check_output(output_text, domain):
    """Run output guardrail check on a string."""
    from hermes.harness import OutputGuardrail

    guard = OutputGuardrail()
    result = guard.check(domain, output_text)
    click.echo(json.dumps(result.model_dump(), indent=2, default=str))


# --------------------------------------------------------------------------- #
# async (delegates to async_engine.cli)
# --------------------------------------------------------------------------- #
@cli.group("async")
def async_cmd():
    """Async engine (smoke test, load test, workflow, worker, orchestrator)."""


@async_cmd.command("ready")
@click.option("--db-path", default="")
@click.option("--workers", default=4, show_default=True)
def async_ready(db_path, workers):
    """Smoke-test the whole async stack on an in-memory bus."""
    from hermes.async_engine.cli import cmd_ready

    agg = cmd_ready(db_path=db_path, workers=workers)
    click.echo(json.dumps(agg, indent=2, default=str))
    click.echo("\nStack OK - orchestrator + workers + store + events all wired.")


@async_cmd.command("loadtest")
@click.option("--store-dir", default="/tmp/hermes-lt")
@click.option("--sizes", default="10,50,100,500", help="Comma-separated task counts")
def async_loadtest(store_dir, sizes):
    """Run the N x workers matrix, print report."""
    from hermes.async_engine.cli import cmd_loadtest

    size_list = [int(s) for s in sizes.split(",")]
    report = cmd_loadtest(store_dir=store_dir, sizes=size_list)
    click.echo(report)


@async_cmd.command("workflow")
@click.option("--db-path", default="")
@click.option("--workers", default=4, show_default=True)
def async_workflow(db_path, workers):
    """Run a small DAG (parallel analyze) end-to-end."""
    from hermes.async_engine.cli import cmd_ready

    agg = cmd_ready(db_path=db_path, workers=workers)
    click.echo(json.dumps(agg, indent=2, default=str))


@async_cmd.command("work")
def async_work():
    """Long-running worker: consume from RabbitMQ + Postgres store."""
    from hermes.async_engine.cli import cmd_work

    cmd_work()


@async_cmd.command("orchestrator")
@click.option("--interval", default=0.5, show_default=True)
def async_orchestrator(interval):
    """Long-running DAG advancer (dispatch ready tasks + finalize workflows)."""
    from hermes.async_engine.cli import cmd_orchestrator

    os.environ["HERMES_ADVANCE_INTERVAL"] = str(interval)
    cmd_orchestrator()


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main():
    cli()


if __name__ == "__main__":
    main()

# Product Spec: Hermes — Agentic AI Orchestration Platform

> **Status**: Draft (multi-domain expansion 2026-09-08)

Hermes began as a procurement orchestration platform (request → DAG →
recommendation → human approval). It is expanding into five business-automation
domains while keeping one shared harness:

1. **AI Advisory Council** — persona advisors (operations/finance/risk/market)
   evaluate a question in parallel; stub-safe, grounded-context aware.
2. **Business Operations Hub** — connectors (CRM/invoicing/calendar/inbox,
   mock-first) aggregated into a severity-sorted "what needs attention" report.
3. **Competitive Intelligence** — collect competitor pages (injectable fetcher)
   → classify (launch/pricing/news/post) → deterministic weekly brief with
   per-finding citations.
4. **Team Knowledge Base** — multi-tenant SOP/decision/client-history docs
   (SQLite + FTS5) with cited, non-fabricated answers.
5. **Second Brain** — personal scope of the same knowledge store (owner or
   explicitly shared only).

**Harness Engineering** underpins all domains: output guardrails (claims with
figures require citations), evaluation metrics (success rate, latency, cost,
error traces) and the existing task lifecycle/tracing.

Grounded-price policy is global: no price/figure may be stated without a
`[source=…]` reference; unreachable sources yield no findings, not guesses.


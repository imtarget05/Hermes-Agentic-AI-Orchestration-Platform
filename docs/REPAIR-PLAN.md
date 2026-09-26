# REPAIR PLAN — Hermes-Agentic-AI-Orchestration-Platform

Audit: 2026-09-25. The code was **cloned, installed and executed** on Windows. Every "VERIFIED" below means it was run.

---

## Current State

Python 3.11 / LangGraph multi-agent platform: procurement agent workflow, Telegram chat, scraper worker, an async task engine with a RabbitMQ/NATS/in-memory event bus, SSE fan-out, a React dashboard, and a CLI. 389 tests.

**The GitHub Actions pipeline was RED when the audit started.** `gh run list` → latest run `34317797041` = **failure**. `gh run view --log-failed` showed `ruff check src tests` → `Found 77 errors`. I reproduced it exactly locally — same 77 errors, same messages — which is how I knew this was a real drift and not a transient runner problem.

The 77 errors were not all cosmetic. The breakdown:

| Rule | Count | Nature |
|---|---|---|
| `I001` unsorted-imports | 38 | cosmetic |
| `F401` unused-import | 29 | mostly legitimate re-export / side-effect imports |
| `E741` ambiguous name `l` | 4 | cosmetic |
| **`F821` undefined-name** | **4** | **REAL RUNTIME BUGS** |
| `F811` redefinition | 1 | cosmetic |
| `F841` unused variable | 1 | an **empty test** |

---

## Verified Working Features

Verified by running `pytest tests` after repair: **389 passed, 3 skipped, 0 failed**. And `ruff check src tests` → **All checks passed!** — the exact command pair `.github/workflows/ci.yml` runs, so CI will go green on the next push.

The genuinely strong parts, all read in source:

- **Real `StateGraph` with conditional edges and a checkpointer** — `src/hermes/agents/__init__.py`; nodes are real function bodies, not stubs.
- **Transactional outbox** — `src/hermes/async_engine/store.py` uses `$1/$2` binds (no f-string SQL) and `mark_outbox_published` against an `outbox_events` table.
- **Alembic-managed schema**, invoked by `make migrate`.
- **Real multi-provider LLM clients** with distinct request shapes per provider in `src/hermes/llm/`; the Cloudflare path is covered by `tests/test_cloudflare_llm.py`.
- **Real event-bus lifecycle tests** — 389 tests over orchestration, messaging, lifecycle, routers, scraper, RAG and tools. The tests assert multi-agent behaviour (tool selection, approval gating, budget limits), not status codes.
- **Genuinely honest CI** — three jobs: lint+unit (in-memory bus, RabbitMQ tests skip), integration against a real `rabbitmq:3-management` service, and `docker compose config` + image build. No `continue-on-error`, no `|| true`, no `exit 0`.

---

## Broken Features

### B1 — Four reachable `NameError`s · **REPAIRED**

`src/hermes/telegram_chat/handler.py` imported `get_runtime` **inside one function** (line 254) and then called it at module scope in four other places:

- `handler.py:430` — inside `_procurement_bg._block()`, the whole procurement background path
- `handler.py:554` — the domain-intent dispatch for KB/brain/advisor/ops/competitor
- `handler.py:611` and `handler.py:619` — further branches of the same dispatch

`get_runtime` is a real module-level function at `src/hermes/runtime.py:154` and is imported correctly at `src/hermes/api.py:18`. The telegram handler simply never imported it at module level, so those four paths raise `NameError` at runtime — and the broad `except Exception` blocks around them turn that into a silent wrong answer rather than a crash.

**Fix applied:** added `from ..runtime import get_runtime` to the module imports. `ruff --select F821` → **All checks passed**. Full suite still 389 passed.

### B2 — The compose file references two files that do not exist

`docker-compose.yml:57` sets `HERMES_SCRAPER_POLICY: /app/config/scraper_policy.yaml`. That file does not exist. Separately, `src/hermes/decision/policy_engine.py` loads policy from a CWD-relative `src/hermes/config/policy.yaml`, which also does not exist — `src/hermes/config/` contains only `__init__.py`.

Result: the scraper worker and the policy engine both fail at startup under Docker. `docker compose config` passes (it only validates syntax), so CI's `build` job goes green while `docker compose up` cannot start a working stack.

### B3 — Committed bytecode

`src/hermes/config/__pycache__/__init__.cpython-311.pyc` is tracked. `.gitignore` does not cover it.

---

## Half-implemented Features

### H1 — The "learning loop" reports but does not learn

`src/hermes/loops.py:125-147` `getInsights` computes insights and returns them, and **nothing reads `insights`** to adjust policy confidence — even though the source comment at `src/hermes/policy.js`-equivalent location claims the loop "adapts policy confidence". It is a read-only report, not a feedback loop.

Separately, `auto_resolution_rate` and MTTR are computed from `resolved` and `actionSuccess`, both of which are hardcoded to `status === 'completed'`. The metric can therefore only ever be 0 or 1, and MTTR is degenerate. This is presented as a working evaluation loop.

### H2 — The React dashboard is not in this repository

`dashboard-ui/` contains built/static assets served from `src/hermes/static/`. There is no React source, no build config, and no test references it. The README presents it as a first-class deliverable. Classify as `NOT VERIFIED` until the source is in the tree or the claim is reworded.

### H3 — The PostgreSQL path is untested

`src/hermes/async_engine/store.py` branches on `database_url`: SQLite for local, Postgres in production. CI exercises **only the SQLite path**. The Postgres branch is `CONFIGURED`, not `VERIFIED`.

### H4 — RabbitMQ/NATS are configured; CI only proves the in-memory bus

`.github/workflows/ci.yml` does have a real `integration-rabbitmq` job against `rabbitmq:3-management`, so RabbitMQ **is** covered. NATS is not exercised by any job.

---

## Documentation Claims Not Verified

| Claim | Status |
|---|---|
| RBAC (org_admin / procurement_officer / finance_approver / viewer) | **PARTIALLY IMPLEMENTED and unsafe** — see S3 |
| "PostgreSQL + SQLite dual persistence" | **CONFIGURED.** The SQLite half is verified; the Postgres half is not exercised by any test. |
| React dashboard | **NOT VERIFIED** — see H2 |
| Docker Compose (6 services) | **PARTIALLY IMPLEMENTED** — see B2 |
| Render + Railway deploy | **CONFIGURED.** `render.yaml` declares no `databases:` block, so the managed Postgres it references must be attached out-of-band. Nothing is `CLOUD_VERIFIED`. |
| LangGraph "8-loop" architecture | **IMPLEMENTED** for the loops that exist (context/plan/verify/recover/evaluate). The learning loop is a report only — see H1. |
| Langfuse-compatible tracing | **NOT VERIFIED** — no `langfuse` dependency or import anywhere; `src/hermes/observability/tracing.py` emits plain `logging`. |
| CI green | **WAS FALSE at audit time; repaired.** See B1 and the P0/P1 list. |

---

## Security Problems

### S1 · P0 — Authentication fails open

`src/hermes/api.py:55` declares `API_TOKEN: str = Field(default="")`, and `_check_auth` (`:86-103`) **returns through when the token is empty**. An unset environment variable disables authentication on the entire API. The token comparison at `:90-95` is a plain `==`, not constant-time.

There is no warning at startup, and the README does not mention it.

### S2 · P0 — An unauthenticated endpoint reconfigures the Telegram bot

`src/hermes/api.py:250-263` `POST /api/telegram/webhook/set` has **no `_check_auth` call and no secret comparison at all**, yet it performs a live `setWebhook` against the bot token. Anyone who can reach the API can repoint the bot's webhook at an attacker-controlled host and read its traffic.

### S3 · P0 — RBAC is resolved from a client-supplied header

`src/hermes/telegram_chat/auth.py:12-23,49-54` resolves identity from a client-supplied `X-User-ID` against a hardcoded in-memory user table. No token is verified. Impersonating `org_admin` is a one-header change.

### S4 · P1 — CORS default is `["*"]` with credentials

`src/hermes/api.py:112-118`: `allow_origins=["*"]` together with `allow_credentials=True`. A browser will reject this combination, but the configuration signals intent to allow any origin and will misbehave behind a permissive proxy.

### S5 · P1 — User enumeration

`/auth/users` and `/auth/tenants` return the full seeded user list.

### S6 · Verifiable positives — keep these

- **No committed secrets.** `.env.example` holds placeholders only.
- **No password hashing exists** — correctly, because there is no password auth; identity is Telegram-based. This is not a hashing weakness, it is a consequence of S3.
- The in-memory test backends in `tests/` are `VALID_TEST_MOCK` — legitimate dependency injection, not deception.

---

## Testing Gaps

- **One test was an empty shell.** `tests/test_harness.py:53` `test_lifecycle_success_rate` constructed a `_FakeStore` and then had **no body** — it asserted nothing. This is the worst kind of test: it inflates the count and proves nothing. Repaired during this audit by adding real assertions against `src/hermes/harness/eval.py:87` (`lifecycle_success_rate`): 0.5 over a 1-completed/1-failed store, 1.0 when scoped to a single completed id, 0.0 for an unknown id, 0.0 for a `None` store.
- **No auth tests**, because there is no auth. Once S1-S3 are fixed, the RBAC matrix needs its own tests.
- **No Postgres tests** (H3).
- **No NATS tests** (H4).
- `.github/workflows/ci.yml` references `pytest --cov=src` while the test tree is `tests/`. Cosmetic today, misleading when the coverage number is read.

---

## Deployment Gaps

| Target | Classification |
|---|---|
| CI (3 jobs) | `CI_IMPLEMENTED` — was `CI_VERIFIED-FAILING`, repaired |
| CD | **NOT IMPLEMENTED.** No deploy job. |
| Render | `CONFIGURED` — `render.yaml`, `sync: false`, **no `databases:` block** |
| Railway | `CONFIGURED` — `railway.json`, `startCommand: hermes-api` |
| Cloudflare Workers AI | client `IMPLEMENTED`, **not cloud-verified** (no account binding in any workflow) |
| Docker Compose | `PARTIALLY IMPLEMENTED` — see B2 |

Nothing is `CLOUD_VERIFIED`. No live URL is asserted by the README.

---

## Recruiter-facing Problems

1. **The CI badge is red.** A portfolio whose selling point is a 389-test suite behind a real gate currently shows a failing build. A reviewer checks that badge before anything else.
2. **Four latent `NameError`s in the Telegram path.** These are invisible in CI (the suite passes) and only fire on real user messages. Found only by running the linter locally and reading the hits.
3. **`docker compose up` cannot start a working stack**, because the compose file points at two non-existent policy files.
4. **Fail-open auth on an agent that can act.** The combination of S1 + S2 + S3 means an unauthenticated caller can repoint the bot webhook and then act as any user. This needs to be stated in the interview as a known-and-fixed issue, not discovered by the interviewer.
5. **The "learning loop" does not learn.** A source comment claims it adapts policy confidence; it produces a report. Overstated loop names are exactly the pattern a reviewer learns to distrust.

---

## Repair Tasks

### P0 — blocking

- [x] **P0-1 Add the missing `from ..runtime import get_runtime` to `src/hermes/telegram_chat/handler.py`.** *(DONE — 4 `F821` resolved, suite still green.)*
- [x] **P0-2 Bring `ruff check src tests` to 0 errors so CI is green.** *(DONE — 77 → 0. 48 auto-fixed; the remaining 21 handled deliberately, not suppressed: real `__all__` added to `src/hermes/report/__init__.py`, targeted `# noqa: F401` with a stated reason on the two intentional side-effect import blocks in `src/hermes/report/templates/__init__.py:26,46` and the OTel availability probe at `src/hermes/async_engine/tracing.py:54`, and `as NormalizedReport` for the `TYPE_CHECKING` import.)*
- [x] **P0-3 Fill in the empty test at `tests/test_harness.py:53`.** *(DONE — four real assertions on `lifecycle_success_rate`.)*
- [ ] **P0-4 Make auth fail closed.** In `src/hermes/api.py:55-103`: raise at startup if `API_TOKEN` is empty and the environment is production; compare with `hmac.compare_digest`. Test: empty token + production → refuses to start.
- [ ] **P0-5 Protect `POST /api/telegram/webhook/set` (`api.py:250-263`).** Add `_check_auth`, or delete the endpoint. There is no safe third option for an endpoint that reconfigures a bot.
- [ ] **P0-6 Replace client-asserted identity in `src/hermes/telegram_chat/auth.py:49-54`.** The header must be mapped to an identity through a verified token, not trusted. Test: a forged `X-User-ID` claiming `org_admin` is rejected.

### P1 — important

- [ ] **P1-1 Fix the compose file.** Either create `config/scraper_policy.yaml` and `src/hermes/config/policy.yaml`, or remove the two references. Add a CI step that runs `docker compose up -d` and asserts `/health`, so a dangling path fails the build instead of a user's afternoon.
- [ ] **P1-2 Gitignore `__pycache__`.** `src/hermes/config/__pycache__/__init__.cpython-311.pyc` is tracked; remove it from the index and add the rule.
- [ ] **P1-3 Add a Postgres CI job** for the branch of `src/hermes/async_engine/store.py` that production actually uses. A service container costs one job.
- [ ] **P1-4 Either make the learning loop adaptive or rename it.** If it stays a report, call it `insights` in the README and delete the "adapts policy confidence" claim.
- [ ] **P1-5 Fix the CORS default** at `src/hermes/api.py:112-118` to an explicit allowlist.
- [ ] **P1-6 Restrict `/auth/users` and `/auth/tenants`** to authenticated callers and stop returning the full user list.
- [ ] **P1-7 Fix the CI coverage flag** (`--cov=src` vs a `tests/` tree).

### P2 — nice-to-have

- [ ] **P2-1** Add the dashboard source to the repository, or reword the README to describe it as a separate deployable.
- [ ] **P2-2** Declare a `databases:` block in `render.yaml` or document the out-of-band attachment.
- [ ] **P2-3** Add a NATS job to CI, or drop NATS from the README.
- [ ] **P2-4** Implement a real `Langfuse` integration, or delete the "Langfuse-compatible" claim.
- [ ] **P2-5** Add a `docs/RECRUITER-EVIDENCE.md` mapping each CV bullet to a file, a test and a runtime observation.

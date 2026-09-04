# Project 02 --- Agentic AI / Multi-Agent Platform

> **Project role:** Agentic AI / AI Engineering project\
> **Primary theme:** Tool use, routing, orchestration, multi-agent
> handoff, task lifecycle, messaging integration.

## 1. Executive Summary

An agentic AI platform designed around multiple specialized agents that
can receive tasks, select tools, coordinate work, hand off subtasks, and
report progress through a gateway/messaging layer.

The project evolved toward an operational agent runtime rather than a
simple chatbot.

## 2. Core Problem

A single general-purpose agent becomes difficult to maintain when: -
tasks have different responsibilities - tools have different
permissions - multiple projects need different routing - long-running
tasks require lifecycle management - agents need to hand work to other
agents - failures need retry/fallback behavior - progress must be
visible outside the local terminal

The architecture therefore separates routing, orchestration, specialized
execution, tools, and reporting.

## 3. High-Level Architecture

``` text
                  ┌─────────────────────┐
                  │ User / Telegram /   │
                  │ External Trigger    │
                  └──────────┬──────────┘
                             ▼
                  ┌─────────────────────┐
                  │   Gateway / Router  │
                  └──────────┬──────────┘
                             ▼
                  ┌─────────────────────┐
                  │ Project Routing     │
                  │ Registry             │
                  └──────────┬──────────┘
                             ▼
                  ┌─────────────────────┐
                  │   Orchestrator      │
                  └──────┬─────┬────────┘
                         │     │
              ┌──────────┘     └──────────┐
              ▼                           ▼
      ┌──────────────┐            ┌──────────────┐
      │ Agent A      │            │ Agent B      │
      │ Research     │            │ Builder      │
      └──────┬───────┘            └──────┬───────┘
             │                           │
             ▼                           ▼
        ┌─────────┐                  ┌─────────┐
        │ Tools   │                  │ Tools   │
        └────┬────┘                  └────┬────┘
             └───────────┬───────────────┘
                         ▼
                  ┌───────────────┐
                  │ Handoff /     │
                  │ Task State    │
                  └───────┬───────┘
                          ▼
                  ┌───────────────┐
                  │ Result /      │
                  │ Progress      │
                  └───────┬───────┘
                          ▼
                  ┌───────────────┐
                  │ Telegram / UI │
                  └───────────────┘
```

## 4. Agent Patterns

### Pattern A --- Orchestrator / Worker

A central orchestrator decomposes a task and delegates work to
specialized agents.

``` text
Task
 ↓
Orchestrator
 ├── Research Agent
 ├── Coding Agent
 └── Validation Agent
 ↓
Aggregate result
```

### Pattern B --- Sequential Pipeline

``` text
Agent A
  ↓
Agent B
  ↓
Agent C
  ↓
Final result
```

Useful when each stage depends on the previous stage.

### Pattern C --- Critic / Debate

``` text
Generator Agent
      ↓
Critic Agent
      ↓
Revision
      ↓
Validated result
```

Useful when correctness matters more than raw generation speed.

## 5. Tool Use

The agent architecture is based on the principle:

``` text
Reason about task
      ↓
Decide whether a tool is needed
      ↓
Select tool
      ↓
Execute
      ↓
Observe result
      ↓
Continue / handoff / finish
```

The key engineering question is not merely "can the model call a tool?"
but:

> Under what conditions is the agent allowed to call the tool, and what
> happens if the tool fails?

## 6. Routing

A project/channel routing registry was designed around:

``` text
Project
  ↓
Channel target
  ↓
Thread ID
```

Known registry concept:

``` text
~/.hermes/projects/routing.json
```

This allows different projects/tasks to route progress to the
appropriate Telegram destination/thread.

## 7. Task Lifecycle

A robust lifecycle should distinguish:

``` text
created
  ↓
queued
  ↓
running
  ↓
handoff
  ↓
retry
  ↓
completed
```

Failure path:

``` text
running
   ↓
failure
   ↓
retryable? ── yes ──> retry
   │
   no
   ▼
failed
```

## 8. Gateway / Messaging

The platform includes a Hermes-style gateway process and Telegram
integration.

Known operational setup: - gateway process - Telegram bot integration -
scheduled gateway execution - allowed-user configuration -
project/channel/thread routing

Secrets such as Telegram bot tokens must never be committed to the
repository.

## 9. Dashboard / Inbox Direction

The project reached a Phase 3 dashboard/inbox task and was moving toward
Phase 4 capabilities:

-   Router Agent
-   multi-agent handoff
-   task lifecycle
-   retry handling

Only mark these as delivered if the corresponding implementation and
tests exist in the repository.

## 10. Tech Stack

Known / intended stack: - Python - Agentic AI architecture - LLM APIs -
tool/function calling - Telegram Bot API - JSON configuration - gateway
process - local/CLI runtime - LangGraph and/or CrewAI concepts where
actually implemented

**Important:** Do not list LangGraph/CrewAI as implemented technology
unless the repository proves it is actually used.

## 11. Outputs

Expected/implemented outputs include: 1. Routed agent task. 2. Tool
execution. 3. Specialized-agent handoff. 4. Task state transitions. 5.
Progress reporting. 6. Telegram notification. 7. Final aggregated
result. 8. Retry/failure handling.

## 12. Features Delivered

-   [x] Tool/function calling architecture
-   [x] Agent routing concept
-   [x] Specialized agent separation
-   [x] Project/channel routing registry
-   [x] Telegram integration
-   [x] Gateway runtime
-   [x] Task lifecycle design
-   [x] Handoff architecture
-   [x] Retry/failure design
-   [ ] Every Phase 4 feature --- verify repository before claiming
    complete

## 13. Engineering Value

This project demonstrates: - Agentic AI - orchestration - tool use -
distributed task thinking - routing - reliability - external
integrations - operational workflows

### One-line positioning

> Built an agentic AI runtime with project-aware routing, tool use,
> multi-agent orchestration, task lifecycle handling, Telegram progress
> reporting, and failure/retry workflows.

## 14. Interview Questions

1.  Why use multiple agents instead of one?
2.  How do you prevent uncontrolled tool calls?
3.  How do you isolate agent permissions?
4.  How is routing represented?
5.  How does handoff work?
6.  What happens when an agent fails?
7.  Which failures are retryable?
8.  How do you avoid infinite retry loops?
9.  How do you persist task state?
10. How would you evaluate an agent?
11. How would you observe an agent in production?
12. How would you prevent prompt/tool injection?
13. When should a multi-agent system be simplified back to one agent?

## 15. Repository Cleanup Policy

Allowed: - temporary task outputs - caches - obsolete debug logs -
abandoned prototypes - duplicate experiments - unused local scripts -
stale generated artifacts

Before deletion: 1. Search imports/references. 2. Search runtime
invocation. 3. Check CI/tests. 4. Check configuration references. 5.
Delete only confirmed dead/unrelated artifacts. 6. Run all relevant
tests. 7. Verify gateway startup and routing.

Never delete: - routing configuration - task schemas - tests - gateway
entrypoints - Telegram integration code - tool definitions - active
agent implementations - dependency/configuration files -
security-related checks

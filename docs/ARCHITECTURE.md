# MAGI Architecture

> The design philosophy, component layout, and core mechanics of MAGI.
> For the high-level vision, see the [README](../README.md).
> For the build plan, see [ROADMAP.md](ROADMAP.md).
> For the current production storage boundary and remaining work, see
> [production-persistence.md](production-persistence.md).
> For the bicultural glossary of product terms, see [terms.md](terms.md).
>
> The original BUS-centric source is preserved in
> [MAGI_BUS_CENTRIC_ARCHITECTURE.md](MAGI_BUS_CENTRIC_ARCHITECTURE.md)
> for reference and diff tracking.

This document is the **authoritative architecture** for MAGI. It is organised
in two parts:

- **Part I — The Agentic Society**: the product model, runtime principle,
  repository layout, three-layer memory, tool pattern, and deployment shape.
- **Part II — BUS-Centric Actor Runtime**: the technical architecture that
  governs inter-module boundaries, durable state, and how a MAGI runtime
  turns input into committed transitions.

`magi.bus` is the **sole public protocol and data-access boundary** between
MAGI modules. Every cross-module state exchange — Agent inbox, Tool calls,
delivery outbox, sessions, settings — flows through a BUS service. The
private SQLite and the MAGIS PostgreSQL cluster are implementation details
hidden behind that facade.

---

# Part I — The Agentic Society

## Agentic Society Model

MAGI is built around the idea of an **agentic society** — not a single agent, not a
chatbot serving a person, but a **group of autonomous agents that form organizations
and act as a collective**.

The society is composed of three layers:

```
  MAGIS                       One MAGI Society; it may have child MAGIS.
    ├── ADAM                  Leading MAGI and control-plane runtime.
    ├── MAGI                  Individual runtimes, including MAGI with the EVA role.
    └── Contacts              People known to a MAGI's private runtime.
```

An agent is not a thread or a session. A **MAGI** has its own container, identity,
LLM configuration and private persistent state.

See [terms.md](terms.md) for the canonical product / society / citizen split and
the ADAM / EVA archetype mapping.

---

## Runtime Principle

**One agent = one container = one runtime process.**

There is one binary (`magi`). At boot, `MAGI_NODE_ROLE` selects the archetype preset
(`adam` or `eve`), which identifies the runtime archetype. The actual role,
instructions and provider configuration are read from the direct MAGIS database.
Every architectural choice is an independent configuration axis:

| Axis | Env var | Default by archetype |
|---|---|---|
| Position | `MAGI_NODE_ROLE` | `adam` = leader, `eve` = member |
| Channels | `settings.channels.enabled` (DB) | seeded `[webui]`; editable in the UI — not a launch flag |
| Private state | `/workspace/memories/magi.db` | SQLite, one replica per MAGI |
| MAGIS database | `MAGIS_DATABASE_URL` | direct MAGIS PostgreSQL Secret |
| ADAM peer | `MAGI_ADAM_URL` | `http://adam:42069` |
| LLM provider | direct MAGIS PostgreSQL | per-MAGI configuration; not injected as an env var |

All persistence — private SQLite and MAGIS PostgreSQL — is reached only through
BUS services. Domain modules (Agent, Tools, Channels, proactive, connectors,
orchestrator) never construct an engine, open a session, or execute a query
directly. The Actor runtime contract in Part II defines how this is enforced.

---

## Repository Layout

```
magi/
├── __main__.py        # composition root; calls bus.bootstrap() to wire workers/adapters
├── bus/               # sole public protocol & data-access boundary
│   ├── services/      # domain-oriented facades (agent_runs, tool_jobs, delivery, ...)
│   ├── repositories/  # durable record CRUD, idempotency keys, leases
│   ├── _persistence/  # engines, ORM base, Alembic — internal
│   ├── bootstrap.py   # wires workers/adapters from configuration
│   └── ...
├── agent/             # reasoning, context, one provider step, AgentWorker
│   ├── step.py        # one provider inference step
│   ├── worker.py      # durable inbox consumer and transition owner
│   ├── memory/        # three-layer memory: session, contacts, self
│   └── llm/           # provider adapters (Anthropic, Minimax, OpenAI) + LLMGateway
├── tools/             # executable registry, discovery, ToolWorker
├── channels/          # protocol adapters and delivery workers
│   ├── dispatcher.py  # D.28 — domain code talks to this, never to adapters
│   ├── ingress/       # normalise external input into AgentMessage via BUS
│   ├── delivery/      # outbox workers per protocol
│   ├── telegram/      # TG bot adapter
│   ├── webui/         # FastAPI app
│   └── tasks/         # scheduled-task CRUD, timing and execution
├── connectors/        # owned external clients (LLM gateways, third-party APIs)
├── orchestrator/      # Kubernetes client; the only process that shapes the cluster
├── proactive/         # proactive policies and task-preset injection
├── prompts/           # central Markdown + YAML prompt corpus and hot-reload loader
└── WebUI/             # React 19 + Vite 5 + Tailwind v4 SPA
```

The legacy `magi.db` package is **removed**, not retained as a compatibility
re-export. Persistence is internal to `magi.bus._persistence` and the
underscore records that Python-level intent; AST import-boundary tests
enforce it (see Part II §3, §14).

---

## Channel Dispatcher (D.28)

The channel dispatcher is the abstraction layer that decouples domain logic from
specific messaging platforms:

```
  domain code (tools, runner, webui, chat send)
     ↓  talks in: uid + channel
  channels/dispatcher.py
     ↓
  ┌──────────┬──────────┬──────────┐
  telegram   slack      wechat     ...
```

Each adapter implements `ChannelAdapter`. Adding a new channel means writing one
adapter and registering it. Core code never changes.

The dispatcher is the **call-site** for delivery; the **durable** path is the
BUS delivery outbox (Part II §12). Inbound is symmetric: every channel
adapter normalises its external event into an `AgentMessage` and submits it
through `bus.agent_runs.publish_input()` — never directly to the AgentWorker.

---

## Agent Loop

`magi.agent.worker.AgentWorker` consumes durable inputs and invokes
`magi.agent.step.run_agent_step()`. Conceptually, one transition is:

1. Claim a durable input through BUS (lease-owned).
2. Validate per-agent credentials (mandatory; no fallback).
3. Assemble context (SOUL.md persona + memory + contacts + skills) via BUS.
4. Run the LLM inference **outside** a transaction — at most one per transition.
5. Persist committed transcript, run state, tool jobs, and outbox effects
   atomically through BUS.
6. Return DTO intents for the next durable input.

Slow work — Tool execution, outbound channel delivery, A2A peer calls — is
represented by durable jobs or outbox effects, never executed inside a
database transaction. Their completion is a later durable input to the same
run. See Part II §6 (Durable Actor runtime) and §9 (Agent lifecycle and
steering) for the full contract.

---

## Persistence (overview)

There are two storage domains. A MAGI's private SQLite is for local runtime state;
its one direct MAGIS PostgreSQL database is for organisation facts. ADAM's child-tree
management permission does not grant it a second runtime database or public mount.

| Domain | Tables / files | Owner |
|---|---|---|
| Private SQLite + `/workspace` | sessions, memory, contacts, tasks, settings, SOUL, skills | one MAGI |
| MAGIS PostgreSQL + `/magis` | `magis`, `magic`, roles, memberships, instructions, providers, `eve_runtimes` | one MAGIS |

BUS is the **only** path that reads or writes either store. The detailed
subdomain → table mapping, the cross-store saga semantics, and the SQLite
deployment constraints live in Part II §5 (Storage domains) and §13 (SQLite,
deployment, and wake-up).

### Private SQLite tables

| Table | Holds |
|---|---|
| `contacts` | Person directory (unified `employees` + `contact_entries` + `user_im_bindings`) |
| `action_items` | Operator to-do inbox |
| `token_usage` | Per-call LLM billing |
| `tasks` / `task_runs` | Scheduled tasks |
| `chat_sessions` / `chat_messages` | Conversation history |
| `chat_messages_fts` | FTS5 trigram full-text search |
| `memory_entries` | MAGI's self-memory |
| `mcp_servers` | Operator-configured MCP servers (name, type, endpoint, env, headers) |
| `meta` / `settings` | KV runtime config |

Private SQLite uses WAL + `busy_timeout=5000` + `BEGIN IMMEDIATE`.

---

## Three-Layer Memory

| Layer | Table | Stores |
|---|---|---|
| Session | `chat_sessions` / `chat_messages` | Conversation history. Auto-compaction keeps context within LLM window |
| Contacts | `contacts` | What the society knows about people. LLM records facts via `add_contact` |
| Self | `memory_entries` | MAGI's own long-term memory. Facts, ongoing work, decisions |

All layers share `Base` and FK to `contacts`, but no inter-layer FKs. Each is
independently searchable.

The committed session transcript is the **only** authority for the next
provider context (Part II §7.2, §10); the in-memory StreamHub view is
best-effort and may be discarded on interruption.

---

## Tools

```python
class Tool(ABC):
    name: str
    description: str
    input_schema: dict

    async def run(ctx: ToolContext, **kwargs) -> ToolResult
```

20+ built-in tools. Lazy-imported via `registry.py`. MCP tools loaded at boot.
Agent-created skills live under `workspace/skills/`.

Tools are **executable code**; the database **Tool Catalog** is the Agent's
single schema authority. The Agent never imports the registry and never knows
whether a schema came from built-in code, MCP, or a Skill (Part II §11).

---

## Unified WebUI and Runtime API

The image has two service roles, selected by command rather than image name:

```text
Browser → magi-webui Service (`magi webui`)
              ├─ React SPA, login, MAGIS/MAGI control API
              └─ signed internal proxy
                     ├─ magi Runtime API (one selected MAGI)
                     └─ magi Runtime API (another selected MAGI)
```

The default `magi` process has no SPA mount and is never the browser entry
point. It serves a private Runtime API so the WebUI can operate on a selected
MAGI's SQLite workspace. The browser does not choose an upstream URL. Instead,
WebUI resolves the selected `magic_id` from the control registry and sends a
short-lived HMAC request bound to method, path, operator and target ID. The
runtime checks that target ID against its `MAGI_RUNTIME_ID` before mapping the
operator into its local contact/session scope.

See [unified-webui.md](unified-webui.md) for the WebUI service shape and
[production-persistence.md](production-persistence.md) for the SQLite ↔
PostgreSQL runtime boundary that the WebUI crosses.

---

# Part II — BUS-Centric Actor Runtime

> **Status: authoritative architecture.** This part supersedes
> `MAGI_BUS_CENTRIC_ARCHITECTURE_REFACTOR_PLAN.md`,
> `MAGI_single_agent_event_driven_runtime_design.md`, and
> `message-driven-agent-runtime.md`.
>
> The original text is preserved verbatim in
> [MAGI_BUS_CENTRIC_ARCHITECTURE.md](MAGI_BUS_CENTRIC_ARCHITECTURE.md).

## 1. Purpose

MAGI is a society of independent runtimes. Each runtime owns private execution
state and exchanges work with users, tools, channels, and peer MAGI runtimes
through durable messages and effects.

This document defines two inseparable rules:

1. **BUS-centric boundaries.** `magi.bus` is the sole public protocol and
   data-access boundary between MAGI modules.
2. **Durable Actor execution.** One MAGI processes one durable input transition
   at a time; slow work is represented by durable jobs or outbox effects and
   never executes inside a database transaction.

The BUS is an in-process Python protocol/data plane, not a required network
service. It is not a business-workflow coordinator: it guarantees durable,
authorised state exchange while an Agent retains reasoning and coordination
decisions.

## 2. Goals and non-goals

The architecture ensures that:

- every module submits, queries, and changes shared state through BUS;
- BUS owns transactions, idempotency, leases, retries, recovery, storage
  routing, data permissions, and persistence invariants;
- Local SQLite and MAGIS PostgreSQL are implementation details behind BUS;
- Agent-visible tool schemas have one durable authority: the Tool Catalog;
- a crash, duplicate delivery, lease expiry, or lost network response does not
  silently lose a committed transition; and
- StreamHub/SSE remains a fast view, while committed state remains authoritative.

It deliberately does not introduce a global broker, a durable LLM worker,
workflow graph, central turn coordinator, shared private storage, or an
end-to-end exactly-once claim.

## 3. Module boundaries

### 3.1 Allowed dependencies

```text
magi.agent.*        -> magi.bus public API + agent internals/LLM providers
magi.tools.*        -> magi.bus public API + tool registry/executors
magi.channels.*     -> magi.bus public API + channel protocol adapters
magi.proactive.*    -> magi.bus public API + its policy code
magi.connectors.*   -> magi.bus public API + owned external clients
magi.orchestrator.* -> magi.bus public API + Kubernetes client

magi.bus services/repositories -> magi.bus._persistence.*
magi.bus._persistence.*        -> Python/SQLAlchemy/database drivers only
```

`magi.__main__` is the composition root. It creates the runtime through
`bus.bootstrap()`, starts workers/adapters, and supplies environment-specific
configuration. A domain module must not initialise a database itself.

### 3.2 Forbidden dependencies

```text
agent      -X-> tools / channels / db
tools      -X-> agent / channels / db
channels   -X-> agent / tools / db
proactive, connectors, orchestrator -X-> db

bus._persistence -X-> agent / tools / channels / proactive / connectors /
                      orchestrator / MCP / Skills
bus             -X-> Tool implementations / channel adapters / AgentWorker /
              LLM providers / Telegram or A2A clients
```

This includes indirect shortcuts. ORM models, sessions, engines, raw SQL
helpers, and database-specific settings remain persistence access even if
re-exported from a different package.

### 3.3 Persistence is internal to BUS

`magi.bus._persistence` contains engine/session factories, ORM base,
database-specific settings, Local SQLite and MAGIS PostgreSQL configuration,
and Alembic support. The leading underscore records the Python-level internal
intent; AST import-boundary tests enforce it. Domain code must only use BUS
service facades.

The composition root and Alembic migration runner are the narrowly scoped
exceptions: they own engine and metadata setup before services exist. They do
not grant persistence access to domain modules. The legacy public `magi.db`
package is removed rather than retained as a compatibility re-export.

### 3.4 BUS public surface

BUS APIs return only immutable dataclasses, Pydantic DTOs, primitives, or
JSON-safe payloads. They never expose an ORM instance, session, connection,
query, lazy relationship, or database-dialect object.

The public API is domain-oriented rather than a God store:

```python
bus.agent_runs.publish_input(...)
bus.agent_runs.claim_next(...)
bus.agent_runs.commit_transition(...)

bus.tool_catalog.replace_snapshot(...)
bus.tool_catalog.list_schemas(...)
bus.tool_jobs.claim_next(...)
bus.tool_jobs.complete(...)
bus.delivery.enqueue(...)
bus.delivery.claim_next(...)
bus.delivery.complete(...)

bus.session.get_transcript(...)
bus.memory.search(...)
bus.settings.get(...)
bus.magis.get_runtime_identity(...)
```

`BusStoreProtocol` is the runtime-facing durable-store contract where the Actor
needs queue and transition primitives. It may be decomposed behind services and
repositories; expanding it into a catch-all application API is not the goal.

## 4. Responsibilities

| Module | Owns | Must not own |
| --- | --- | --- |
| `magi.bus` | contracts, services, repositories, queues, leases, outbox, transactions and invariants | LLM inference, Tool implementation, channel/A2A I/O, Agent decisions |
| `magi.bus._persistence` | SQLAlchemy models/base, engines/sessions, Alembic and database configuration | domain commands or public application API |
| `magi.agent` | reasoning, context, one provider step, AgentWorker | direct DB access, Tool execution, channel delivery |
| `magi.tools` | executable registry, discovery, ToolWorker | Agent schema authority, direct DB/channel access |
| `magi.channels` | protocol ingress, delivery workers and adapters | Agent orchestration, Tool execution, DB access |
| `magi.proactive` | policy and event production | persistence that bypasses BUS |

BUS stores and routes state but does not decide which MAGI should act, how
MAGI should divide work, or whether ordinary language is a cancellation.

## 5. Storage domains

Each MAGI has two stores, implemented by `magi.bus._persistence` and accessed
by domain code only through BUS:

| BUS subdomain | Store | Responsibility |
| --- | --- | --- |
| inbox, runs, continuations, LLM attempts | Local SQLite | private Actor execution state |
| tool catalog, calls and jobs | Local SQLite | schema authority and tool effects |
| delivery outbox and A2A invocations | Local SQLite | reliable external delivery |
| sessions, memory, contacts, tasks, settings, MCP config | Local SQLite | private product state |
| MAGIS/MAGIC, memberships, roles, admins | MAGIS PostgreSQL | organisation facts and permissions |
| provider configuration and runtime identity | MAGIS PostgreSQL | MAGIS control-plane facts |

BUS presents a uniform facade but must not represent two commits as one
cross-database transaction. Cross-store work is a saga:

```text
local transaction -> durable outbox -> worker -> MAGIS transaction
                  -> durable result/event through BUS
```

## 6. Durable Actor runtime

A MAGI is an Actor with a private durable mailbox. One runtime owns one active
Agent transition and active run at a time; different MAGI runtimes proceed
independently. Tool and delivery jobs can run concurrently under their own
resource limits.

```text
WebUI / Telegram / Tasks / Proactive / A2A ingress
                            |
                            v
                    BUS durable agent inbox
                            |
                            v
          AgentWorker: one message, one transition
             |          |                       |
             v          v                       v
       LLM + StreamHub tool jobs         delivery/A2A outbox
             |          |                       |
             +----------+-----------------------+
                            |
                            v
                    durable result/input
```

Each transition means:

```text
claim durable input
  -> query required state through BUS
  -> at most one complete LLM inference outside a transaction
  -> atomically commit state and subsequent jobs/outbox through BUS
```

The Actor never waits synchronously for a Tool, an A2A peer, or outbound
delivery. Their completion is a later durable input to the same run.

## 7. Contracts and durable records

### 7.1 Input envelope

Agent input is a versioned DTO such as `AgentMessage`. It includes a stable
producer-supplied `event_id`/idempotency key and causality metadata:

```text
event_id, kind, source_type, source_id, external_event_id
conversation_id, run_id/target_run_id
correlation_id, causation_id, reply_to
caller identity/role, deadline, payload, metadata
```

`source_type + source_id + external_event_id` is an additional deduplication
key when the upstream system has a stable ID. `correlation_id` traces work;
`reply_to` binds Tool or A2A results to the original effect.

The durable inbox covers at least:

```text
channel.message.received, task.triggered,
tool.result, tool.failed,
run.steer, run.cancel,
a2a.request, a2a.result
```

### 7.2 Separate queues and projections

One SQLite file does not mean one universal `messages` table. Each durable
record has its own producer, consumer, lease, and invariant:

| Record | Producer | Consumer | Responsibility |
| --- | --- | --- | --- |
| `agent_inbox` | channels, scheduler, ToolWorker, A2A ingress | AgentWorker | Agent input |
| `agent_runs` | AgentWorker | AgentWorker | continuation and run state |
| `run_inputs` | channels/BUS | AgentWorker | steering/control inputs |
| `tool_calls` / `tool_jobs` | AgentWorker | ToolWorker/AgentWorker | tool effects and aggregation |
| `a2a_invocations` | Agent and A2A workers | AgentWorker | delegation/continuation binding |
| `delivery_outbox` | Agent/Tool transition | delivery worker | committed external delivery |
| `llm_attempts` | AgentWorker | AgentWorker/WebUI | diagnostics and stream lifecycle |
| `session_messages` | committed transition | context builder/WebUI | transcript, not a queue |

Queue records need a stable ID, status, availability time, lease owner/expiry,
attempt count, error, timestamps, and appropriate unique constraints. `run_inputs`
preserves actual receipt order; `session_messages` retains provider-native
assistant/tool-use/tool-result blocks, metadata, call IDs, and ordering.

### 7.3 Run invariants

An `agent_run` stores status (`queued`, `running`, `waiting_tool`,
`waiting_a2a`, `completed`, `failed`, `cancelled`), continuation, pending effect
IDs, deadline, version, iteration count, and failure details. `received_seq`
is the real arrival order; `context_seq` is the order content enters the
provider transcript. They must remain distinct.

## 8. Transaction, lease, and recovery protocol

Workers use short transactions around a long-running external operation:

```text
1. Claim: claim eligible work, write a processing lease, increment attempts, commit.
2. Execute: LLM inference, Tool execution, or network delivery outside a transaction.
3. Complete: persist outcome, create next durable records, complete/retry/dead-letter, commit.
```

For an Agent, `commit_agent_transition()` atomically writes the committed
transcript projection, run/continuation, tool calls/jobs, A2A invocations,
delivery records, LLM attempt outcome, and completed inbox state. A failed
commit leaves no partial transition or orphaned effect.

Tool completion updates its job/call and writes the matching `tool.result` or
`tool.failed` inbox input in one transaction. Delivery completion happens only
after the external sender reports success.

The reliability model is **at-least-once delivery plus idempotent consumption**.
Lease expiry, bounded exponential retry, dead-letter handling, and startup
recovery are ordinary control paths. External effects should receive the stable
idempotency key where supported; residual uncertainty must be recorded where
they cannot.

No database transaction may contain LLM inference, Tool execution, Telegram or
A2A HTTP, SSE/WebSocket writes, or another unbounded wait.

## 9. Agent lifecycle and steering

`AgentWorker` claims an input, queries context through BUS, performs at most
one provider inference, and returns DTO intents for transition commit. It does
not mutate a session, execute a Tool, or send a channel message directly.

- When idle, the next eligible external input starts a run.
- During inference, new input is durable but cannot interrupt the stream or
  start a second inference.
- During an active same-conversation run, ordinary input becomes a durable
  steering input. It neither cancels Tools nor creates a parallel run.
- Another conversation remains queued until the active run finishes.
- Only explicit control, including `POST /api/runs/{run_id}/cancel`, creates
  `run.cancel`; ordinary language never implies cancellation.

After tool use, the next provider input has this order:

```text
user request -> assistant tool-use -> tool results by original ordinal
             -> steering inputs by received_seq -> next inference
```

All required tool calls reach a real terminal state before continuation. This
keeps provider transcripts valid and avoids falsely reporting an already-run
external effect as cancelled.

## 10. LLM streaming and committed truth

The Actor calls `LLMGateway.stream()` directly for its one inference. Provider
deltas are normalised and sent through `StreamHub`. Every stream event has a
`run_id`, `llm_attempt_id`, and sequence information for client deduplication.

StreamHub and SSE are in-memory, best-effort views. Deltas are never individual
SQLite records, inbox messages, or outbox deliveries. The authoritative result
is the full provider response committed by the transition; only then is
`message.committed` emitted. On interruption, clients discard the uncommitted
draft and recovery creates a new attempt ID. Reconnection reads durable run and
transcript state through BUS.

## 11. Tool Catalog and ToolWorker

The database Tool Catalog is the only Agent schema source:

```text
registry / MCP / Skill discovery -> BUS snapshot -> Local SQLite
Agent                           <- BUS list_schemas
ToolWorker                       -> executable registry only
```

A definition includes name, source, description, JSON schema, allowed roles,
enabled state, implementation version, schema hash, and revision. Snapshot
replacement validates definitions, upserts current entries, disables entries
missing from the source, advances revision/hash, then commits atomically.

BUS performs role filtering from catalog data. Agent code neither imports the
registry nor knows whether a schema came from built-in code, MCP, or a Skill.
A tool job persists definition identity, catalog revision/schema hash, provider
tool-call ID, arguments, and idempotency key. A missing, disabled, or
incompatible tool produces a durable provider-valid failure, never a crash or
silent execution.

Tool implementations use their registry only for execution. All product-state
queries and cross-module effects use BUS; a Tool does not call a channel
dispatcher directly.

## 12. Channels, delivery, and A2A

Channels normalise external input into `AgentMessage` and submit it through
BUS. A successful ingress means durable acceptance, not synchronous Agent
completion. WebUI returns `202 Accepted` with `run_id`, observes best-effort
SSE, and reads the durable result when necessary.

Agent and Tool effects enter `delivery_outbox` through BUS. Delivery workers
claim records, call their protocol adapter outside a transaction, then complete,
retry, or dead-letter through BUS. They never invoke Agent code or read ORM
state.

A2A is accept-then-process:

```text
source Agent transition -> durable A2A delivery -> HTTP POST with event ID
  -> target A2A ingress durably publishes request -> target returns 202
  -> target Actor runs independently -> durable result/failure returns to source
```

`202 Accepted` confirms only committed receipt. `message_magi` is one-way by
default. An explicit `expect_reply` persists an `a2a_invocation` with the run,
reply target, deadline, and idempotency key; only a matching `reply_to` result
may resume a `waiting_a2a` run. A2A is HTTP between private runtimes, not a
shared database or token-stream transport.

## 13. SQLite, deployment, and wake-up

Private SQLite is the execution authority for one MAGI because it can atomically
commit a transition and its local jobs/outbox. Connections use WAL, foreign
keys, a busy timeout, bounded `SQLITE_BUSY` retry, short transactions, and
queue/idempotency/ordering indexes. Workers use independent connections.

One SQLite file permits one writable runtime:

```text
replicas: 1
exclusive PVC
StatefulSet or Deployment Recreate strategy
startup lease/attempt recovery; graceful shutdown stops new claims
```

Concurrent Pods and network-filesystem sharing are prohibited. If a MAGI later
needs active multi-Pod processing, shared mailboxes, or sustained SQLite
contention, replace the BUS repository/store implementation; do not alter
Agent, Tool, or Channel contracts.

BUS commits durable work before signaling an in-memory wake-up. Bounded polling
and startup recovery are the fallback: a missed wake-up may delay work but
cannot lose a committed record.

## 14. Verification and completion

AST import-boundary checks must forbid all dependencies in section 3, including
direct imports of `magi.bus._persistence`, type-only imports, and known dynamic
imports, with no permanent allowlist. Tests cover
catalog role filtering, ORM isolation, idempotency, leases, crashes, duplicate
and late Tool/A2A results, steering order, delivery retry, restart recovery,
and that LLM/Tool/network operations occur outside transactions.

The architecture is fully converged only when Agent, Tools, Channels, proactive
code, connectors, and orchestrator have no direct DB access or cross-domain
imports; both storage domains are BUS-only; the Tool Catalog is the sole Agent
schema source; and all docs describe `handle_message()` only as removed legacy
behaviour or a BUS-contained temporary wrapper.

Do not claim completion by deleting tests, weakening assertions, copying DB
access into another module, or combining wholesale path moves with behavioural
rewrites.

> A MAGI is an independent durable Actor. BUS is the sole public boundary for
> cross-module state and messages. The Actor serially turns durable input into
> durable state and effects; external Tool, channel, and peer work occurs
> outside transactions and returns through the same boundary.

---

# Glossary

- **MAGI** — The general kind of autonomous agent in this system.
- **MAGIS** — A MAGI Society. A group of MAGI that forms a tree via `parent_id`.
- **MAGIC** — Internal table/API name for an individual MAGI; not a separate product term.
- **ADAM** — Leading MAGI role for its direct MAGIS. MAGIS administrator grants
  are direct-only and do not inherit across the society tree.
- **EVA** — Default working MAGI role. Executes tasks and collaborates.
- **Role** — ADAM and EVA are reserved roles; a MAGIS can also define custom roles.
- **Contact** — A person known to the society. Role: `admin` (WebUI operator), `assigned` (the served user), or `guest` (everyone else).
- **BUS (`magi.bus`)** — The sole public protocol and data-access boundary
  between MAGI modules. Holds contracts, services, repositories, queues,
  leases, outbox, transactions, and persistence invariants.
- **Actor** — A MAGI runtime that owns one private durable mailbox, processes
  one durable input transition at a time, and emits durable state and effects.
- **Transition** — One complete claim → execute → commit cycle for an Agent
  input; the atomic unit of Agent work.
- **Durable input** — A row in a BUS-owned queue (e.g. `agent_inbox`,
  `run_inputs`) that a worker may claim; idempotency and lease-protected.
- **Outbox effect** — A committed durable record (e.g. `delivery_outbox`,
  `a2a_invocations`, `tool_jobs`) that defers slow external work to a
  worker.
- **Tool Catalog** — The Local SQLite table that is the only Agent schema
  authority; populated from registry / MCP / Skill discovery via BUS.
- **StreamHub** — The in-memory, best-effort view of provider deltas; never
  the authority for state.
- **A2A** — Agent-to-Agent communication between MAGI peers via
  accept-then-process HTTP with HMAC-signed, idempotent events.
- **Saga** — Cross-store work modelled as local transaction → outbox →
  worker → other-store transaction → result/event through BUS; never as
  a single cross-database transaction.
- **`BusStoreProtocol`** — The runtime-facing durable-store contract that
  the Actor uses for queue and transition primitives; may be decomposed
  behind services and repositories.

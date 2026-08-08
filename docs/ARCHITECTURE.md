# MAGI Architecture

> The design philosophy, component layout, and core mechanics of MAGI.
> For the high-level vision, see the [README](../README.md).
> For the build plan, see [ROADMAP.md](ROADMAP.md).
> For the current production storage boundary and remaining work, see
> [production-persistence.md](production-persistence.md).
> For the bicultural glossary of product terms, see [terms.md](terms.md).
> For the local standalone deployment plan, see
> [MAGI_LOCAL_STANDALONE_DEPLOYMENT_IMPLEMENTATION_PLAN.md](MAGI_LOCAL_STANDALONE_DEPLOYMENT_IMPLEMENTATION_PLAN.md).
> For detailed per-module responsibilities, see
> [MAGI_MODULE_RESPONSIBILITIES_AND_DEPENDENCIES.md](MAGI_MODULE_RESPONSIBILITIES_AND_DEPENDENCIES.md).
>
> The original BUS-centric architecture source is preserved in
> [MAGI_BUS_CENTRIC_ARCHITECTURE.md](MAGI_BUS_CENTRIC_ARCHITECTURE.md).

This document is the **authoritative architecture** for MAGI. It is organised
in three parts:

- **Part I — The Agentic Society**: the product model, runtime principle,
  repository layout, three-layer memory, tool pattern, and deployment shape.
- **Part II — BUS-Centric Module Model**: inter-module boundaries, dependency
  rules, per-module responsibilities, and the BUS as sole protocol plane.
- **Part III — Durable Actor Runtime**: how a MAGI runtime turns input into
  committed transitions — the transaction protocol, queues, catalog, delivery,
  streaming, and recovery.

`magi.bus` and `magi.prompts` are the **only shared modules** across domain
code. BUS holds cross-module contracts, queues, outbox, and persistence
invariants; Prompts holds reusable content (SOUL, compaction, titles,
templates). Private SQLite and MAGIS PostgreSQL are implementation details
hidden behind BUS.

---

# Part I — The Agentic Society

## Agentic Society Model

MAGI is built around the idea of an **agentic society** — not a single agent, not a
chatbot serving a person, but a **group of autonomous agents that form organizations
and act as a collective**.

```
  MAGIS                       One MAGI Society; it may have child MAGIS.
    ├── ADAM                  Leading MAGI and control-plane runtime.
    ├── MAGI                  Individual runtimes, including MAGI with the EVA role.
    └── Contacts              People known to a MAGI's private runtime.
```

An agent is not a thread or a session. A **MAGI** has its own container (or
host process), identity, LLM configuration and private persistent state.

See [terms.md](terms.md) for the canonical product / society split and
the ADAM / EVA archetype mapping.

---

## Runtime Principle

**One agent = one container = one runtime process.**

There is one binary (`magi`). All startup-related code — config parsing,
path derivation, bootstrap, runtime composition, local process management,
Kubernetes resource creation, WebUI lifecycle — lives in a single package,
`magi.startup` (see [Part IV — Unified Startup](#part-iv--unified-startup)).
There is no parallel Runtime / CLI / Kubernetes startup layer; the entire
contract is four inputs:

| Input | Purpose | Default |
|---|---|---|
| `HOST_WORKSPACE_DIR` | Operator-side root of persistent data | `~/.magi` (Linux) / `~/Documents/.magi` (macOS, Windows) |
| `MAGI_NAME` | Display name (participates in workspace derivation) | `eva-000` |
| `MAGIS_DATABASE_URL` | MAGIS DSN; omit ⇒ bootstrap the first MAGIS | unset |
| `MAGI_ID` | Persistent identity when joining an existing MAGIS | unset |

The on-disk workspace is **derived** — `HOST_WORKSPACE_DIR / "MAGI_Citizens"
/ MAGI_NAME` — and is never configured directly. Runtime Host / Port and
the Reload flag are hardcoded by the runtime role; there is no operator
knob. ADAM / EVA archetypes are no longer a boot-time env selector — the
actual role, instructions and provider configuration are read through BUS
from the MAGIS database, so two containers running the same image can hold
either role without any deploy-time branch.

Other architectural axes that remain:

| Axis | Source | Default |
|---|---|---|
| Channels | `settings.channels.enabled` (DB via BUS) | seeded `[webui]`; editable in the UI — not a launch flag |
| Private state | `<workspace>/memories/magi.db` | one SQLite per MAGI, derived from `HOST_WORKSPACE_DIR` + `MAGI_NAME` |
| MAGIS database | `MAGIS_DATABASE_URL` | direct MAGIS PostgreSQL (K8s) or separate SQLite (Local) |
| LLM provider | MAGIS database (via BUS) | per-MAGI configuration; not injected as an env var |

All persistence — private SQLite and MAGIS database — is reached only
through BUS. Domain modules (Agent, Tools, Channels, proactive,
connectors, orchestrator) never construct an engine, open a session, or
execute a query directly.

---

## Deployment Profiles

MAGI supports three deployment modes, sharing the same binary and module
boundaries:

| | Kubernetes (production) | k8s-dev (kind) | Local (non-container) |
|---|---|---|---|
| One MAGI = | Pod + PVC + ClusterIP Service | Pod + hostPath workspace | independent OS process + workspace directory + localhost port |
| MAGIS storage | PostgreSQL Deployment | PostgreSQL (kind) | separate SQLite database |
| MAGIS workspace | shared PVC | shared hostPath | shared directory |
| WebUI entry | `magi webui` Service | kind NodePort 30069→42069 | `127.0.0.1:42069` |
| Orchestrator | K8s ServiceAccount | K8s ServiceAccount (kind) | none (each MAGI is its own process) |

**CLI Profile process model** (each MAGI is an independent process):

The CLI Profile is driven by the unified `magi.startup.cli` verbs:

```text
magi run                   # bootstrap + serve one MAGI in-process (default: eva-000)
magi create --name eva-001 # register a new MAGI under an existing MAGIS
magi start  --name X       # spawn a detached subprocess for one MAGI
magi stop   --name X
magi restart --name X
magi status --name X
magi webui                 # boot the singleton WebUI in-process
```

Each MAGI is its own independent OS process with its own workspace,
SQLite, internal Runtime port, logs, and provider configuration. Process
management lives in `magi.startup.local`; PID files are pinned to
`<workspace>/run/magi.pid` and logs to `<workspace>/logs/stdout.log`
plus `<workspace>/logs/stderr.log`. The WebUI lifecycle belongs to the
**host** workspace root (`~/.magi/run/webui.pid` plus
`~/.magi/logs/webui.*.log`) because it is shared by the whole MAGIS, not
by any one MAGI. CLI Profile is a trusted single-user mode; it provides
no container-level isolation.

---

## Repository Layout

```
magi/
├── __main__.py        # thin CLI shim; forwards verbs to magi.startup.cli
├── startup/           # ALL startup code lives here — see Part IV
│   ├── config.py      #   StartupConfig (the 4 startup inputs) + validation
│   ├── paths.py       #   all on-disk path derivation
│   ├── context.py     #   StartupContext (post-bootstrap frozen handle)
│   ├── bootstrap.py   #   first-MAGI vs join-MAGI bootstrap (idempotent)
│   ├── runtime.py     #   run_magi() — bus + workers + channels + api composition
│   ├── local.py       #   create / start / stop / restart / status for local MAGI
│   ├── webui.py       #   singleton WebUI lifecycle (local process + K8s)
│   ├── kubernetes.py  #   PVC / Deployment / Service / WebUI resource creation
│   └── cli.py         #   magi run | create | start | stop | restart | status | webui
├── bus/               # sole public protocol & data-access boundary
│   ├── contracts/     # immutable DTOs, AgentMessage, queue records
│   ├── services/      # domain-oriented facades (agent_runs, tool_catalog, delivery, ...)
│   ├── models/        # ORM models (owned by bus, persisted by db)
│   ├── bootstrap.py   # wires workers/adapters from configuration
│   └── _persistence/  # engines, ORM base, Alembic — internal to bus
├── agent/             # reasoning, context, one provider step, AgentWorker
│   ├── step.py        # one provider inference step
│   ├── worker.py      # durable inbox consumer and transition owner
│   └── llm/           # provider adapters (Anthropic, Minimax, OpenAI)
├── tools/             # executable registry, discovery, ToolWorker
├── channels/          # protocol adapters and delivery workers
│   ├── api/           # shared HTTP API (WebUI backend, A2A ingress, …)
│   ├── tasks/         # generic scheduler Worker (consumes task commands via BUS)
│   ├── telegram/      # TG bot adapter
│   ├── a2a/           # Agent-to-Agent channel
│   └── delivery/      # outbox delivery workers
├── skills/            # SKILL.md loader, catalog, and load_skill tool
├── mcp/               # MCP Server adapter (connects MCP tools into Tools)
├── connectors/        # product-specific tool adapters (Gmail, Calendar, …)
├── proactive/         # system-level tasks and heartbeats (enhances Agent initiative)
├── orchestrator/      # K8s / local process lifecycle client + Worker
├── plugins/           # plugin discovery and lifecycle (BUS-only)
├── db/                # SQLAlchemy models, engines, migrations (BUS-only)
├── prompts/           # central Markdown + YAML prompt corpus and hot-reload loader
├── types.py           # shared dataclasses (ToolContext, ToolResult)
└── WebUI/             # React 19 + Vite 5 + Tailwind v4 SPA
```

The legacy `magi.launcher`, `magi.runtime` composition module and the
`CLIProcessRuntimeBackend` abstraction are removed; their responsibilities
are consolidated into `magi.startup`.

---

## Channel Dispatcher (D.28)

Domain code talks to the dispatcher, never to a specific adapter:

```
  domain code (tools, runner, webui, chat send)
     ↓  talks in: uid + channel
  channels/dispatcher.py
     ↓
  ┌──────────┬──────────┬──────────┐
  telegram   slack      wechat     ...
```

Each adapter implements `ChannelAdapter`. Adding a channel means writing one
adapter and registering it. Inbound is symmetric: every adapter normalises
input into an `AgentMessage` and submits it through BUS — never directly to
the AgentWorker.

---

## Agent Loop

`magi.agent.worker.AgentWorker` consumes durable inputs and invokes
`magi.agent.step.run_agent_step()`:

1. Claim a durable input through BUS (lease-owned).
2. Validate per-agent credentials (mandatory; no fallback).
3. Assemble context (SOUL + instructions + memory + contacts + skills) via BUS.
4. Run the LLM inference **outside** a transaction — at most one per transition.
5. Persist committed transcript, run state, tool jobs, and outbox effects
   atomically through BUS.
6. Return DTO intents for the next durable input.

Slow work — Tool execution, outbound delivery, A2A peer calls — is represented
by durable jobs or outbox effects, never executed inside a database transaction.

---

## Persistence

Two storage domains, both reached only through BUS:

| Domain | Tables / files | Owner |
|---|---|---|
| Private SQLite + `/workspace` | sessions, memory, contacts, tasks, settings, SOUL, skills | one MAGI |
| MAGIS database + `/magis` | `magis`, `magic`, roles, memberships, instructions, providers, `eva_runtimes` | one MAGIS |

### CLI Profile storage

```text
HOST_WORKSPACE_DIR/                (default: ~/.magi on Linux;
                                   ~/Documents/.magi on macOS / Windows)
├── MAGI_Societies/<magis_id>-<slug>/  # one SQLite per MAGIS
│   └── magis.db                   # organisation + control-plane state
├── MAGI_Citizens/<name>/          # workspace derived from MAGI_NAME
│   ├── magi.db / runtime.json     # private SQLite + identity snapshot
│   ├── skills/
│   ├── SOUL.md
│   ├── logs/
│   └── run/                       # per-MAGI PID + lock files
└── run/webui.pid                  # singleton WebUI pid (host-level, not per-MAGI)
└── logs/webui.{stdout,stderr}.log # singleton WebUI logs (host-level)
```

The on-disk layout is **derived** from `HOST_WORKSPACE_DIR` + `MAGI_NAME`
through `magi.startup.paths`. There is no final-workspace CLI flag. The
WebUI lifecycle state lives on the host root because WebUI is owned by the
MAGIS, not by any single MAGI; per-MAGI PID and log files live inside the
per-MAGI workspace.

MAGIS data is never written into a MAGI's private `magi.db`; each MAGIS
has its own SQLite file with WAL, busy timeout, and foreign keys. The
`workspace/memories/magi.db` convention is identical across all three
deployment modes — K8s Pods resolve `<workspace>` from the PVC mount path
passed in as `HOST_WORKSPACE_DIR`, CLI processes use `HOST_WORKSPACE_DIR`
on the host.

### Private SQLite tables

| Table | Holds |
|---|---|
| `contacts`, `contact_notes` | Person directory |
| `action_items` | Operator to-do inbox |
| `token_usage` | Per-call LLM billing |
| `tasks` / `task_runs` | Scheduled tasks |
| `chat_sessions` / `chat_messages` | Conversation history |
| `chat_messages_fts` | FTS5 trigram full-text search |
| `memory_entries` | MAGI's self-memory |
| `mcp_servers` | Operator-configured MCP servers |
| `tools` | Tool Catalog (Agent's schema source) |
| `skills` | Skill Catalog (system prompt source) |
| `meta` / `settings` | KV runtime config |

SQLite uses WAL + `busy_timeout=5000` + `BEGIN IMMEDIATE`. One writable
runtime per file.

---

## Three-Layer Memory

| Layer | Table | Stores |
|---|---|---|
| Session | `chat_sessions` / `chat_messages` | Conversation history. Auto-compaction keeps context within LLM window |
| Contacts | `contacts` | What the society knows about people. LLM records facts via tools |
| Self | `memory_entries` | MAGI's own long-term memory. Facts, ongoing work, decisions |

All layers share `Base` and FK to `contacts`, but no inter-layer FKs.
The committed session transcript is the only authority for the next provider
context; StreamHub views are best-effort.

---

## Tools

```
class Tool(ABC):
    name: str
    description: str
    input_schema: dict

    async def run(ctx: ToolContext, **kwargs) -> ToolResult
```

20+ built-in tools. MCP tools loaded at boot via the MCP adapter. Agent-created
skills live under `workspace/skills/`.

The database **Tool Catalog** is the Agent's single schema authority. The
Agent never imports the tool registry and never knows whether a schema came
from built-in code, MCP, or a Skill.

Tool implementation sources obey a tiered model:

```
MCP Server ──→ magi.mcp ──┐
product API ─→ connectors ─┼──→ magi.tools → BUS Tool Catalog / jobs
built-in / Skill ──────────┘
```

`magi.mcp` adapts MCP connections into Tools contracts. `magi.connectors`
wraps product-specific APIs as Tools. Both depend on Tools; Tools never
depends back on MCP or connectors.

---

## Unified WebUI and Runtime API

Two service roles from one image:

```text
Browser → magi-webui Service (`magi webui`)
              ├─ React SPA, login, MAGIS/MAGI control API
              └─ signed internal proxy
                     ├─ magi Runtime API (one selected MAGI)
                     └─ magi Runtime API (another selected MAGI)
```

The `magi` process has no SPA mount. It serves a private Runtime API so the
WebUI can operate on a selected MAGI's workspace. The browser does not choose
an upstream URL; WebUI resolves the selected `magic_id` from the registry and
sends a short-lived HMAC request bound to method, path, operator and target ID.

See [unified-webui.md](unified-webui.md) for the WebUI service shape.

---

# Part II — BUS-Centric Module Model

`magi.bus` and `magi.prompts` are the **only shared modules** across domain
code. Cross-module state exchange flows through BUS. Arrow `A → B` means **A
may import and depend on B**; it does not represent runtime message flow.

## Dependency Graph

```mermaid
flowchart TD
    WEB["WebUI frontend"] --> API["magi.channels.api"]
    PRO["magi.proactive"] --> TASKS["magi.channels.tasks"]
    MCP["magi.mcp"] --> TOOLS["magi.tools"]
    CONNECTORS["magi.connectors"] --> TOOLS

    API --> BUS["magi.bus"]
    TASKS --> BUS
    OTHER["Other channels"] --> BUS
    AGENT["magi.agent"] --> BUS
    TOOLS --> BUS
    PLUGINS["magi.plugins"] --> BUS
    ORCH["orchestrator"] --> BUS

    BUS --> DB["magi.db"]
    AGENT -.-> PROMPTS["magi.prompts"]
    PRO -.-> PROMPTS
```

Only `magi.bus` depends on `magi.db`. `magi.prompts` depends on no MAGI
module. The narrower `MCP → Tools`, `proactive → channels.tasks`, and
`WebUI → channels.api` edges are explicit extension relationships, not
permission to treat Tools or Channels as general shared APIs.

## Forbidden Dependencies

```text
agent      -X-> tools / channels / plugins / db
tools      -X-> agent / channels / plugins / MCP / db
channels   -X-> agent / tools / plugins / db
plugins    -X-> agent / tools / channels / MCP / db

proactive  -X-> bus / db / agent / tools
MCP        -X-> bus / db / agent / channels
WebUI      -X-> bus / db / agent / tools

connectors, orchestrator -X-> db

db         -X-> bus / agent / tools / channels / proactive / plugins / MCP
bus        -X-> agent / tools / channels / proactive / plugins / MCP / prompts
             / Tool implementations / LLM providers / protocol clients
```

This includes indirect shortcuts. ORM models, sessions, engines, and raw SQL
helpers remain persistence access even if re-exported from a different package.

## Allowed Dependencies Matrix

| Caller | Allowed | Forbidden |
|---|---|---|
| `agent` | `bus`, `prompts` | `tools`, `mcp`, `connectors`, `channels`, `plugins`, `db` |
| `tools` | `bus` | `agent`, `mcp`, `connectors`, `channels`, `plugins`, `db` |
| `mcp` | `tools` | `bus`, `agent`, `connectors`, `channels`, `plugins`, `db` |
| `connectors` | `tools` | `bus`, `agent`, `mcp`, `channels`, `plugins`, `db` |
| `channels.api` | `bus` | `agent`, `tools`, `mcp`, `tasks`, `plugins`, `db` |
| `channels.tasks` | `bus` | `agent`, `tools`, `mcp`, `plugins`, `db` |
| other `channels.*` | `bus` | `agent`, `tools`, `mcp`, `plugins`, `db` |
| `proactive` | `tasks`, `prompts` | `bus`, `agent`, `tools`, `db` |
| `plugins` | `bus` | `agent`, `tools`, `mcp`, `connectors`, `channels`, `db` |
| `bus` | `db` | all domain modules |
| `db` | stdlib/SQLAlchemy | all MAGI business modules |
| `prompts` | stdlib | all MAGI business modules |

## Per-Module Responsibilities

### `magi.bus`

**Owns:** cross-module contracts, services, repositories, queues, leases,
outbox, transactions, idempotency, retries, recovery, storage routing, data
permissions, and persistence invariants.

**Must not:** execute LLM inference, run Tool implementations, perform
channel/A2A I/O, make Agent decisions, or contain ORM models (those belong
to `magi.db`).

BUS APIs return only immutable dataclasses, Pydantic DTOs, primitives, or
JSON-safe payloads. They never expose ORM instances, sessions, connections,
queries, or dialect objects.

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

### `magi.db`

**Owns:** SQLAlchemy models, engines, sessions, Alembic migrations, database
configuration, and repository implementations.

**Must not:** define cross-module business protocols, decide message routing,
or be imported by any domain module (only BUS). Types must not be re-exported
through public packages.

### `magi.prompts`

**Owns:** system prompts, SOUL, context blocks, compaction templates, chat
title prompts, task templates, and bot reply strings.

**Must not:** call LLMs, read sessions or databases, publish events, or
depend on any MAGI business module. It is a content resource, not a
coordination layer.

### `magi.agent`

**Owns:** reasoning loop, context assembly, one provider step, `AgentWorker`,
LLM provider adapters, and stream handling.

**Must not:** call Tool implementations directly, deliver channel messages,
access the database, maintain the Tool Catalog, or perform plugin discovery.

**Depends on:** `magi.bus`, `magi.prompts`.

### `magi.tools`

**Owns:** unified Tool descriptor, parameter schema, execution result protocol,
built-in Tool registry, ToolWorker, Tool Catalog sync to BUS, tool permission
and timeout control. Provides stable extension contracts for MCP and connectors.

**Must not:** initiate Agent reasoning, modify session context, access the
database, or depend on MCP/connectors (they are upstream adapters).

**Depends on:** `magi.bus`.

Tool classification:
- **Core built-in**: stateless, reusable across products (read_file, bash, search_sessions, …)
- **Connector tools**: product-specific wrappers owned by a `magi.connectors` module
- **MCP tools**: provided by external servers, adapted through `magi.mcp`

Core Tools must not accumulate product-specific logic.

### `magi.mcp`

**Owns:** MCP server configuration, the durable :class:`McpWorker` that
holds every MCP server connection, and the small loader primitives
(``MCPServerConnection`` / ``MCPTool`` / ``MCPTimeoutConfig``) the
worker composes. Configuration lives in the new_bus
``McpServerBook`` (table `mcp_servers`); runtime change notifications
flow through the new_bus ``mcpServerChangedJobBoard``.

**Lifecycle:** the worker is constructed by the composition root
(``magi.startup.runtime._runtime_lifespan`` / ``worker_lifespan``)
right after the Tools worker, reads enabled rows from
``bus.mcp_servers_book``, opens every connection in parallel, and
re-injects the discovered tools via
:func:`magi.tools.registry.register_tools` under source
``"mcp"``. The four MCP manage tools
(``add_mcp_server`` / ``list_mcp_servers`` /
``update_mcp_server`` / ``delete_mcp_server``) live under
:mod:`magi.tools.mcp` and are registered as builtins —
they publish to the ``mcpServerChangedJobBoard``, and the
McpWorker applies the write + reconnects as the sole writer
of :class:`McpServerBook`. The Tools
worker's existing ``on_tools_changed`` listener takes care of
republishing the catalog.

**Must not:** register tools directly outside of
:func:`magi.tools.registry.register_tools`, hold module-level
caches of connections, or be imported by Tools core.

**Depends on:** `magi.tools` (extension contracts only),
`magi.bus` (settings_book for timeouts),
`magi.new_bus` (McpServerBook + mcpServerChangedJobBoard).

### `magi.connectors`

**Owns:** product-specific tool groups with shared auth, configuration, and
lifecycle. Wraps product APIs/SDKs/CLIs into Tools contracts.

**Must not:** define a parallel tool protocol, access DB directly, depend on
Channels/Plugins/MCP, or push product logic into core Tools.

**Depends on:** `magi.tools`.

### `magi.channels`

Channels normalise external input into BUS protocol and convert BUS output
into channel-specific formats. They are boundary adapters, not business logic.

**Owns:** input validation/normalisation, BUS command/event submission, outbox
delivery consumption, channel-level auth, rate limiting, and connection
lifecycle.

**Must not:** invoke Agent directly, execute Tools, access the database, or
decide Agent reasoning steps.

#### `magi.channels.api`

The WebUI backend. Provides HTTP, WebSocket, and SSE endpoints for session
queries, message submission, run status, streaming output, and management
operations. The WebUI frontend's sole backend boundary.

#### `magi.channels.tasks`

A generic scheduler Worker. Consumes `task.schedule/update/cancel/pause/resume`
commands from BUS, manages schedule definitions, and publishes standard
`agent.input` events on expiry.

**Must not:** contain preset tasks, proactive policies, Agent prompts, or
direct Agent/Tool calls. `correlation_id` links commands to result events
for synchronous callers.

```
API / Tools / Proactive
     ↓ task.schedule / update / cancel / pause / resume
    BUS
     ↓
channels.tasks Worker
     ↓ task.scheduled / updated / rejected 等结果事件
    BUS
     ↓ 到期时发布标准 agent.input
 AgentWorker
```

### `magi.proactive`

**Owns:** system-level task and heartbeat definitions. Decides what proactive
tasks to create based on policy, state, or external events.

**Must not:** create Agent runs directly, call Agent/Tools/Channels, access
DB, or implement task delivery (that's `channels.tasks`' job).

**Depends on:** `magi.channels.tasks`, `magi.prompts`.

### `magi.plugins`

**Owns:** plugin discovery, validation, enable/disable, manifest/version
management, and capability registration through BUS.

**Must not:** import Agent, Tools, or Channels directly. Even if a plugin
provides tools, connectors, or channel capabilities, the plugin manager
remains BUS-only.

**Depends on:** `magi.bus`.

### `magi.orchestrator`

**Owns:** the K8s control-plane worker that handles constrained runtime
lifecycle operations (create, start, stop, delete MAGI instances) via the
Kubernetes API. The Orchestrator Worker consumes lifecycle commands from
BUS and writes results back through BUS. BUS never imports the K8s
client; the client never accesses the registry ORM directly.

```text
WebUI → channels.api → BUS → Orchestrator Worker → Kubernetes API
                              ↓
                       BUS 状态/结果事件
```

The CLI Profile has **no orchestrator backend** — each MAGI is its own
OS process, spawned by `magi.startup.local` (subprocess + PID file) and
optionally registered as a systemd unit. The legacy
`CLIProcessRuntimeBackend` abstraction has been removed; there is no
single `RuntimeBackend` protocol with multiple implementations — local
process management and K8s resource creation live side by side under
`magi.startup` (`.local` and `.kubernetes`) without sharing a polymorphic
surface.

### `magi.__main__` (Composition Root)

**Owns:** argument parsing, environment configuration, creating BUS/DB,
assembling workers and adapters, starting runtime/channel processes, managing
startup order, health checks, and graceful shutdown.

**Must not:** implement message routing, Agent reasoning, Tool execution,
or Channel business logic. It can import all modules for assembly but must
not become a data-passing middle layer.

---

## Runtime Collaboration Flows

### User message → reply

```
WebUI → channels.api → BUS (agent.input) → AgentWorker
  → BUS (read context) → LLM inference → BUS (commit transcript)
  → channels.api (SSE/WS push) → WebUI
```

API and Agent never call each other directly.

### Agent tool call

```
AgentWorker → BUS (write tool call/job) → ToolWorker
  → execute tool → BUS (tool result) → AgentWorker (resume run)
```

Agent reads the Tool Catalog from BUS, never imports the registry.

### MCP tool discovery and execution

```
MCP Server → McpWorker.connect → magi.mcp.MCPServerConnection → MCPTool
  → magi.tools.registry.register_tools("mcp", ...)
  → on_tools_changed → ToolsWorker republishes catalog
  → ToolWorker claims job → Tools → MCPTool → session.call_tool
  → MCP Server → BUS (tool result)
```

The McpWorker is the sole owner of MCP connections and the sole
writer to ``McpServerBook``; the loader no longer carries a
module-level connection cache. The LLM manage tools (under
``magi.tools.mcp``) publish ``McpServerChangedJob`` to the
``mcpServerChangedJobBoard`` and await the Worker's result —
they do not write the Book directly. The WebUI API endpoints
(``magi.channels.api.mcp_servers``) still write through the old
bus ``McpService`` until they are migrated to new_bus
(TODO: mcp-worker integration).

### Scheduled task

```
API / Tools / Proactive → BUS (task.schedule) → channels.tasks Worker
  → persist schedule → wait expiry → BUS (agent.input)
  → AgentWorker → BUS (result)
```

The scheduler is generic; `proactive` defines system-level tasks and
heartbeats. They do not call each other directly.

---

# Part III — Durable Actor Runtime

> This part defines the technical Actor model, queue architecture,
> transaction protocol, and recovery semantics.

## Purpose

MAGI is a society of independent runtimes. Each runtime owns private execution
state and exchanges work with users, tools, channels, and peer MAGI runtimes
through durable messages and effects.

Two inseparable rules:

1. **BUS-centric boundaries.** `magi.bus` is the sole protocol and data-access
   boundary between MAGI modules.
2. **Durable Actor execution.** One MAGI processes one durable input transition
   at a time; slow work is represented by durable jobs or outbox effects and
   never executes inside a database transaction.

The BUS is an in-process Python protocol/data plane, not a required network
service. It guarantees durable, authorised state exchange while an Agent
retains reasoning and coordination decisions.

## Goals

- Every module submits, queries, and changes shared state through BUS.
- BUS owns transactions, idempotency, leases, retries, recovery, storage
  routing, data permissions, and persistence invariants.
- Local SQLite and MAGIS database are implementation details behind BUS.
- Agent-visible tool schemas have one durable authority: the Tool Catalog.
- A crash, duplicate delivery, lease expiry, or lost network response does not
  silently lose a committed transition.
- StreamHub/SSE remains a fast view, while committed state remains authoritative.

## Durable Actor Runtime

A MAGI is an Actor with a private durable mailbox. One runtime owns one active
Agent transition at a time; different MAGI runtimes proceed independently.

```
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

Each transition:

```
claim durable input
  → query required state through BUS
  → at most one complete LLM inference outside a transaction
  → atomically commit state and subsequent jobs/outbox through BUS
```

The Actor never waits synchronously for a Tool, an A2A peer, or outbound
delivery. Their completion is a later durable input to the same run.

## Input Envelope

Agent input is a versioned DTO with a stable producer-supplied
`event_id`/idempotency key and causality metadata:

```
event_id, kind, source_type, source_id, external_event_id
conversation_id, run_id/target_run_id
correlation_id, causation_id, reply_to
caller identity/role, deadline, payload, metadata
```

Durable inbox kinds:

```
channel.message.received, task.triggered,
tool.result, tool.failed,
run.steer, run.cancel,
a2a.request, a2a.result
```

## Separate Queues and Projections

One SQLite file does not mean one universal `messages` table:

| Record | Producer | Consumer | Responsibility |
|---|---|---|---|
| `agent_inbox` | channels, scheduler, ToolWorker, A2A ingress | AgentWorker | Agent input |
| `agent_runs` | AgentWorker | AgentWorker | continuation and run state |
| `run_inputs` | channels/BUS | AgentWorker | steering/control inputs |
| `tool_calls` / `tool_jobs` | AgentWorker | ToolWorker | tool effects |
| `a2a_invocations` | Agent, A2A workers | AgentWorker | delegation binding |
| `delivery_outbox` | Agent/Tool transition | delivery worker | external delivery |
| `llm_attempts` | AgentWorker | AgentWorker/WebUI | diagnostics |
| `session_messages` | committed transition | context builder | transcript |

## Run Invariants

An `agent_run` stores status (`queued`, `running`, `waiting_tool`,
`waiting_a2a`, `completed`, `failed`, `cancelled`), continuation, pending effect
IDs, deadline, version, iteration count, and failure details. `received_seq`
is the real arrival order; `context_seq` is the order content enters the
provider transcript. They must remain distinct.

## Transaction, Lease, and Recovery Protocol

```
1. Claim: claim eligible work, write a processing lease, increment attempts, commit.
2. Execute: LLM inference, Tool execution, or network delivery outside a transaction.
3. Complete: persist outcome, create next durable records, complete/retry/dead-letter, commit.
```

For an Agent, `commit_agent_transition()` atomically writes the committed
transcript, run/continuation, tool calls/jobs, A2A invocations, delivery
records, LLM attempt outcome, and completed inbox state.

Reliability model: **at-least-once delivery plus idempotent consumption**.
Lease expiry, bounded exponential retry, dead-letter handling, and startup
recovery are ordinary control paths.

No database transaction may contain LLM inference, Tool execution, Telegram or
A2A HTTP, SSE/WebSocket writes, or another unbounded wait.

## Agent Lifecycle and Steering

- When idle, the next eligible external input starts a run.
- During inference, new input is durable but cannot interrupt the stream.
- During an active same-conversation run, ordinary input becomes a durable
  steering input. It neither cancels Tools nor creates a parallel run.
- Another conversation remains queued until the active run finishes.
- Only `POST /api/runs/{run_id}/cancel` creates `run.cancel`; ordinary
  language never implies cancellation.

After tool use, provider input order:

```
user request → assistant tool-use → tool results by original ordinal
             → steering inputs by received_seq → next inference
```

## LLM Streaming and Committed Truth

Provider deltas are normalised through `StreamHub`. Every stream event carries
`run_id`, `llm_attempt_id`, and sequence information. StreamHub and SSE are
in-memory, best-effort views. The authoritative result is the full provider
response committed by the transition; only then is `message.committed` emitted.

## Tool Catalog and ToolWorker

```
MCP discovery → Tools extension contract ──┐
built-in / Skill discovery ────────────────┤
                                            ├→ Tools → BUS snapshot → SQLite
Agent ← BUS list_schemas                    │
ToolWorker → executable registry only       │
```

A definition includes name, source, description, JSON schema, allowed roles,
enabled state, implementation version, schema hash, and revision. Snapshot
replacement validates definitions, upserts current entries, disables missing
entries, advances revision/hash, then commits atomically.

## Channels, Delivery, and A2A

Channels normalise input into `AgentMessage` and submit through BUS. WebUI
returns `202 Accepted` with `run_id`, exposes best-effort SSE, and reads the
durable result through BUS.

A2A is accept-then-process:

```
source Agent transition → durable A2A delivery → HTTP POST with event ID
  → target A2A ingress durably publishes request → target returns 202
  → target Actor runs independently → durable result returns to source
```

`message_magi` is one-way by default. An explicit `expect_reply` persists an
`a2a_invocation` with the run, reply target, deadline, and idempotency key.

## SQLite, Deployment, and Wake-up

Private SQLite is the execution authority because it can atomically commit a
transition and its local jobs/outbox. WAL, foreign keys, busy timeout, bounded
`SQLITE_BUSY` retry, short transactions, and queue indexes.

One SQLite file = one writable runtime. No concurrent Pods or processes, no
network filesystem sharing. Local MAGIS SQLite uses the same WAL/busy/FK
configuration.

Path resolution: every deployment mode — K8s Pod, k8s-dev Pod, local
process — uses the **same** `HOST_WORKSPACE_DIR` + `MAGI_NAME` derivation
through `magi.startup.paths`. K8s orchestrator passes `HOST_WORKSPACE_DIR`
as the PVC mount root; local processes set it on the host. There is no
hardcoded `/workspace` path anywhere in the codebase, and there is no
final-workspace CLI flag.

BUS commits durable work before signaling an in-memory wake-up. Bounded polling
and startup recovery are the fallback.

## CLI Deployment Security

CLI Profile is a trusted single-user mode:

- The Runtime binds a fixed internal host + port; WebUI exposes
  `127.0.0.1` by default. Both addresses are hardcoded — there is no
  operator knob to override Host or Port.
- Control secret uses cryptographically secure random generation.
- Provider API keys never enter CLI argv, logs, or launch JSON.
- Runtime proxy validates HMAC, target runtime ID, and freshness.
- Workspace paths use canonicalisation and boundary checks.
- Each MAGI is an independent OS process — no supervisor tree, no `shell=True`.
- Delete defaults to archive, not permanent workspace removal.
- Documentation clearly states CLI Profile is not a security sandbox.

## Completion Criteria

The architecture is converged when:

- No domain module imports `magi.db`, ORM models, or sessions directly.
- `agent`, `tools`, `channels`, `plugins`, and `proactive` have no
  cross-module direct imports; runtime collaboration flows through BUS.
- `tools` has no dependency on `mcp` or `connectors`; they are upstream adapters.
- `proactive` does not access BUS or DB directly; it enters through `channels.tasks`.
- `channels.tasks` is a generic scheduler with no preset tasks or policies.
- `channels.tasks` publishes standard `agent.input` on expiry, never calls Agent directly.
- Agent reads Tool Catalog from BUS, never imports the registry.
- StreamHub views are best-effort; committed state is authoritative.
- All cross-worker state changes are recoverable from BUS/DB.
- `magi.__main__` is a thin CLI shim; all composition lives in `magi.startup.runtime`.
- All startup code (config, paths, bootstrap, runtime, local, webui,
  kubernetes, cli) lives in one package: `magi.startup`.
- `magi run` boots the first MAGI (`eva-000`) by default; subsequent
  MAGIs join via `magi run --name X --magis … --magi-id …`.
- One WebUI per MAGIS, started with the first MAGI; subsequent MAGIs do
  not start a second WebUI.
- Architecture import rules are enforced by automated tests.

---

# Part IV — Unified Startup

> The startup domain — how a MAGI is created, bootstrapped, and brought up,
> together with the one and only WebUI of a MAGIS — is consolidated in a
> single package, `magi.startup`. Everything else in Parts I–III assumes that
> package produced a ready `StartupContext`; this Part explains why the
> startup domain is shaped the way it is, and what its actual surface is.

## One Binary, One Package, Four Inputs

The `magi` binary is the **only** runtime binary. It serves two service
roles (`magi` boots a MAGI Runtime; `magi webui` boots the singleton WebUI
control plane). All startup logic — config parsing, path derivation,
bootstrap, runtime composition, local process management, Kubernetes
resource creation, and WebUI lifecycle — lives in **one package**:
`magi.startup`. There are **no parallel** Runtime / CLI / Kubernetes startup
modules and **no abstract backend** polymorphism with a single
implementation.

The startup contract has exactly four inputs:

| Input | Purpose | Default |
|---|---|---|
| `HOST_WORKSPACE_DIR` | Operator-side root of persistent data | `~/.magi` (Linux) / `~/Documents/.magi` (macOS, Windows) |
| `MAGI_NAME` | Display name; participates in workspace derivation | `eva-000` (the first MAGI) |
| `MAGIS_DATABASE_URL` | MAGIS DSN; **omit ⇒ bootstrap the first MAGIS** | unset |
| `MAGI_ID` | Persistent identity when joining an existing MAGIS | unset |

The on-disk workspace **is derived**, never passed in:

```text
workspace_dir = HOST_WORKSPACE_DIR / "MAGI_Citizens" / MAGI_NAME
```

Local, container and Kubernetes all use the exact same derivation. There is
no final-workspace CLI flag, env var, or config key. Workspace identity is
verified against the persisted identity file on disk; conflict fails rather
than overrides.

## Distinguishing "Bootstrap MAGIS" from "Join MAGIS"

The single judgement that decides which path runs is:

```text
Is MAGIS_DATABASE_URL provided?
```

- **No MAGIS**: this is the first MAGI. The bootstrap creates the MAGIS
  database, the Genesis MAGIS, the `eva-000` identity, its Membership, sets
  it as ADAM of Genesis, prepares its private workspace, starts its Runtime,
  and **starts the singleton WebUI**. Every step is idempotent: re-running
  `magi run` does not create a second Genesis, a second `eva-000`, or a
  second WebUI.
- **MAGIS provided**: this MAGI joins an existing MAGIS. The startup loads
  the persistent identity by `MAGI_ID`, validates Membership and Role,
  validates that the workspace on disk matches the same `MAGIS_DATABASE_URL`
  + `MAGI_ID`, then starts the Runtime. It **never** creates a new MAGIS, a
  Genesis, a second ADAM, an unknown identity, or a second WebUI. A bad
  `MAGI_ID` fails the boot — the Runtime does not auto-register itself.

## First-MAGI Constraint and WebUI Singleton

The first MAGI is always `eva-000`. Combining `MAGIS_DATABASE_URL=…` with
`MAGI_NAME != "eva-000"` is rejected by config validation.

The whole MAGIS has **exactly one WebUI**. WebUI is the only externally
exposed service; it is brought up with the first MAGI (`eva-000`) and
recovered together with it. Any subsequent MAGI never starts a second WebUI
— neither on local processes nor as a second Kubernetes Deployment /
Service. The WebUI PID and logs sit on the **host** workspace root
(`~/.magi/run/webui.pid`, `~/.magi/logs/webui.{stdout,stderr}.log`) precisely
because WebUI is owned by the MAGIS, not by any one MAGI.

## Package Layout

```text
magi/startup/
├── config.py        # StartupConfig — the four inputs, validation, defaulting
├── paths.py         # all on-disk path derivation (host / workspace / DB / PID / logs)
├── context.py       # StartupContext — frozen output of bootstrap, consumed by runtime
├── bootstrap.py     # first-MAGI vs join-existing-MAGI bootstrap, idempotent
├── runtime.py       # Runtime composition (bus, workers, channels, api, lifespan)
├── local.py         # local process management — create / start / stop / restart / status
├── webui.py         # singleton WebUI lifecycle — local process or K8s Deployment
├── kubernetes.py    # K8s resource creation — PVC / Deployment / Service / WebUI
└── cli.py           # unified CLI: magi run | create | start | stop | restart | status
```

`magi/__main__.py` is intentionally thin — it only parses the legacy
`magi [runtime|webui|cli] [--check]` form and forwards to the corresponding
`magi.startup.cli` command. All composition lives behind `magi.startup`.

## `StartupConfig` and `StartupContext`

`StartupConfig` is the single frozen source of truth for all startup inputs:

```python
@dataclass(frozen=True)
class StartupConfig:
    host_workspace_dir: Path     # default: ~/.magi
    magi_name: str               # default: "eva-000"
    magis_database_url: str | None  # None ⇒ bootstrap first MAGIS
    magi_id: str | None          # required when joining an existing MAGIS

    @property
    def workspace_dir(self) -> Path:
        # Always derived — never configurable directly.
        return host_workspace_dir / "MAGI_Citizens" / magi_name

    @property
    def is_first_magi(self) -> bool:
        return self.magis_database_url is None
```

`StartupContext` is what the rest of the system actually consumes. It is
built once, after bootstrap, and carries everything Runtime / Channels /
Tools / BUS need to do their job:

```text
StartupContext
├── host_workspace_dir, workspace_dir
├── magi_name, magi_id (persistent identity)
├── magis_database_url, private_database_url
└── is_first_magi
```

Code never re-reads or re-mutates env vars during execution; the context is
the only handle the runtime hands to its subsystems.

## Bootstrap Flows

Both flows are idempotent. The full lifecycle they protect:

```
MAGIS workspace, Genesis row, EVA-000 identity, EVA-000 ADAM Membership,
private SQLite file, runtime.json identity snapshot — all written with
on-conflict-do-nothing semantics. Restarting `magi run` is safe.
```

When joining an existing MAGIS:

```
1. connect MAGIS_DATABASE_URL
2. load MAGI by MAGI_ID  → fail if not found (no auto-registration)
3. verify direct Membership and Role
4. validate MAGI_NAME matches the persisted name
5. validate the on-disk workspace identity (magi_id + magis_database_url)
   matches the supplied ones — conflict fails the boot
6. initialize the per-MAGI private database and prepare the workspace
```

If the workspace on disk holds one identity and the boot args supply a
different one, **boot fails** — the system never overwrites an existing
identity with another.

## Runtime Composition

`magi.startup.runtime.run_magi(config)` is the only way to run a MAGI:

```text
context = bootstrap_magi(config)
bus      = build_bus(context)
workers  = build_workers(context, bus)
channels = build_channels(context, bus)
api      = build_runtime_api(context, bus)

async with runtime_lifespan(...):
    await serve_runtime_api(api)
```

The runtime module is responsible for **running one MAGI**. It does not
spawn child MAGIs, manage PID files, create Kubernetes resources, run the
WebUI, or accept runtime-level Host / Port / Reload knobs.

## Local vs Kubernetes

Local and Kubernetes differ only in the **outer resource layer**:

| | Local (CLI) | Kubernetes |
|---|---|---|
| Outer resource prep | directories + subprocess | PVC + Deployment + (internal) Service |
| Runtime entrypoint | `magi run` / `magi start --name …` (managed by `magi.startup.local`) | container `command: ["magi"]` with env set by orchestrator |
| WebUI | `magi webui` subprocess managed by `magi.startup.webui` | a single `magi-webui` Deployment + external Service, created once with `eva-000` |
| Process model | independent OS process per MAGI | independent Pod per MAGI |

Beyond that layer, both paths run the **same** `bootstrap → runtime` flow
with the same configuration contract.

In Kubernetes, the orchestrator (`magi.startup.kubernetes`) creates the
PVC, the MAGI Deployment, the internal Service and, only on the first
deployment of `eva-000`, the WebUI Deployment and the external Service.
The Deployment passes the four contractual env vars (`HOST_WORKSPACE_DIR`,
`MAGI_NAME`, `MAGIS_DATABASE_URL`, `MAGI_ID`); it never passes Host,
Port, Reload, or the final workspace path.

## Network Boundaries

- **Runtime**: fixed internal host + port, hardcoded. Different Pods have
  separate network namespaces, so there is no conflict. The Runtime is
  reachable only as a ClusterIP; it is never exposed externally.
- **WebUI**: the **only** externally exposed component. One per MAGIS,
  always paired with `eva-000`. Receives browser traffic, terminates the
  proxy, then signs every internal request HMAC-bound to method, path,
  operator and selected `magic_id`.
- **Reload**: hardcoded by the runtime role (production off,
  development on); there is no operator knob.

## CLI Surface

The unified CLI verbs live in `magi.startup.cli`:

```bash
magi run                  # bootstrap + serve one MAGI in-process
magi create               # register a new MAGI under an existing MAGIS
magi start   --name X     # spawn a detached subprocess for one MAGI
magi stop    --name X     # SIGTERM one MAGI's subprocess (+ WebUI if first)
magi restart --name X     # stop + start
magi status  --name X     # list local slots and liveness
magi webui                # boot the singleton WebUI in-process
```

The legacy `magi [runtime|webui|cli]` form is preserved as a thin shim
that forwards to `magi.startup.cli`; it is **not** the canonical
interface.

---

# Glossary
- **ADAM** — Leading MAGI role for its direct MAGIS. MAGIS administrator grants
  are direct-only and do not inherit across the society tree.
- **EVA** — Default working MAGI role. Executes tasks and collaborates.
- **Contact** — A person known to the society. Role: `admin` (WebUI operator),
  `assigned` (the served user), or `guest` (everyone else).
- **BUS (`magi.bus`)** — The sole public protocol and data-access boundary
  between MAGI modules. Holds contracts, services, repositories, queues,
  leases, outbox, transactions, and persistence invariants.
- **Prompts (`magi.prompts`)** — The sole shared content resource module.
  Holds prompt templates, SOUL, context blocks, and bot reply strings.
- **Actor** — A MAGI runtime that owns one private durable mailbox, processes
  one durable input transition at a time, and emits durable state and effects.
- **Transition** — One complete claim → execute → commit cycle for an Agent
  input; the atomic unit of Agent work.
- **Tool Catalog** — The database table that is the only Agent schema
  authority; populated from registry / MCP / Skill discovery via BUS.
- **StreamHub** — The in-memory, best-effort view of provider deltas; never
  the authority for state.
- **A2A** — Agent-to-Agent communication via accept-then-process HTTP with
  HMAC-signed, idempotent events.
- **Saga** — Cross-store work modelled as local transaction → outbox →
  worker → other-store transaction → result/event through BUS.
- **Composition Root** — `magi.__main__` and `magi.runtime`; the only places
  that instantiate and wire all modules. They do not carry business logic.

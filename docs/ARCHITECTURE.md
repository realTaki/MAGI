---
title: Architecture
description: The authoritative architecture for the MAGI runtime.
permalink: /architecture/
---

# MAGI Architecture

The current MAGI runtime is organised around one durable boundary — **BUS**
(`magi.bus`) — that owns Books (typed CRUD), Job Boards (publish → claim →
submit_result), and the file-backed prompt/skill shelves. Inside that
boundary the package is split in two: **bases** (`magi/bus/bases/`) hold
the Job/Book contracts and the database integration (engines, `Base`,
`FileShelf`) without any table or column definitions; **firmwares**
(`magi/bus/firmwares/`) hold the concrete Jobs, Books, and their
schema/Alembic revisions. This document is
the authoritative architecture for the current runtime: canonical naming,
dependency rules, runtime shape, composition root, durable invariants, and
how each domain package attaches to BUS.

## Status and terminology

Use these names consistently in code, tests, operational material, and
user-facing documentation. They are the only supported names.

| Concept | Name |
| --- | --- |
| Architecture boundary | **BUS** |
| Public Python package | `magi.bus` |
| Process-local facade | `Bus` (frozen dataclass returned by `open_bus`) |
| Composition-root entry | `open_bus(workspace_dir=…, magis_url=…)` |
| Control-plane entry | `open_bus(magis_url=…)` (no workspace) |
| Durable CRUD / query API | **Book** (e.g. `conversations_book`, `messages_book`) |
| Durable `publish → claim → submit_result` API | **Job Board** (e.g. `agent_job_board`) |
| Ephemeral notification aid | `stream_hub` (not a source of truth) |

`open_bus(magis_url=…)` returns a deliberately narrower `MagisBus`: a
database-only control-plane facade, not a second node runtime or compatibility
layer. It does not create a workspace, local SQLite database, file shelf, or
workers. There is no fallback singleton or dual-write path in the current
runtime.

The naming has two generations: the live `magi.bus` is the evolved, adopted
successor of the previous **new BUS** work; `magi.new_bus` is the next
**MAGI-BUS vNext** iteration now being developed alongside it. The latter is
not a retired compatibility package. It currently has its own `Bus`,
`BusForWorker`, Dock/Slot lifecycle, storage backends, and Firmware contract; its
design baseline lives in
[`magi/new_bus/MAGI-BUS 架构设计书.md`](../magi/new_bus/MAGI-BUS%20架构设计书.md).
The production runtime described below is still composed through
`magi.bus.bootstrap.open_bus(...)` until that migration is explicitly wired
through the startup composition root.

Identifiers follow one name per concept — `magi_id`, `contact_id`,
`conversation_id`, `job_id`, `tgid` — across ORM columns, DTO fields,
parameters, and API payloads. The full table, including the retired names
(`magic_id`, `uid`, `session_id`, `event_id`, agent-context `run_id`) that
still appear in git history and old database dumps, is in
[MAGI terms]({{ '/terms/#canonical-id-names' | relative_url }}).

## Runtime shape

```text
                ┌─────────────┐
                │  Operator   │
                │   WebUI     │
                └──────┬──────┘
                       │ cookie + selected MAGI
                       ▼
                ┌─────────────┐
                │ magi-webui  │  (singleton, browser-facing)
                │  proxy +    │
                │  control    │
                └──────┬──────┘
                       │ signed proxy → /api/runtime/<magi_id>/…
                       ▼
                ┌─────────────────────────────────────┐
                │       one MAGI process per node     │
                │                                     │
                │   HTTP API  ◄── FastAPI on sticky port
                │   Workers   ◄── async claim loops
                │      │
                │      ▼
                │   ┌──────┐                          │
                │   │ Bus  │  Books + Job Boards +    │
                │   │      │  StreamHub + Prompt/Skills│
                │   └──┬───┘                          │
                │      │                              │
                │   private SQLite  +  MAGIS database │
                └─────────────────────────────────────┘
```

A single process owns one runtime `Bus`; the facade is built by
`magi.bus.bootstrap.open_bus(...)` and injected (by constructor) into every
Worker. There is no process-global `BUS` singleton. `magi.new_bus` is the
separate vNext implementation and is not yet the runtime composition root.

## Composition root

`magi.startup.runtime.run_magi` is the single composition root for one MAGI
process. It:

1. Resolves the MAGIS URL and opens one `Bus` via
   `open_bus(workspace_dir=…, magis_url=…)`; the BUS
   derives its local SQLite path as `<workspace>/memories/magi.db`.
2. Reads `RuntimeSpec` through that same Bus and builds the `StartupContext`
   (paths, MAGI name, MAGI identity, database URLs, runtime port).
3. Validates the runtime identity against the provisioned MAGIS records
   (`memberships_book`, `control_runtimes_book`, `port_allocations_book`).
4. Constructs a `WorkerRegistry(bus, …)` — see
   `magi.startup.workers.WorkerRegistry`.
5. Starts the workers in dependency order.
6. Serves the private Runtime FastAPI app (`create_runtime_app`) on the
   sticky runtime port.

```text
WorkerRegistry (composition root)
 ├─ providers   — ProvidersWorker           (always)
 ├─ tools       — ToolsWorker               (always)
 ├─ mcp         — McpWorker                 (always)
 ├─ agent       — AgentWorker               (always)
 ├─ task        — TaskWorker                (enabled_channels ⊇ {"task", "scheduled"})
 ├─ tg          — TelegramWorker            (enabled_channels ⊇ {"tg", "telegram"})
 ├─ webui       — WebUIWorker               (always)
 └─ proactive   — ProactiveWorker           (always)
```

Channel workers are conditional: Telegram and TaskWorkers start only when
`bus.settings_book["channels.enabled"]` lists the channel. WebUI is always
enabled by the composition root (the persisted and fallback default is
`["webui"]`). A2A is the **MAGIS-internal persistent communication channel**
between MAGI of the same Society: messages are persisted into the shared
MAGIS database via two job boards (`a2a_request_job_board` for one-shot
questions, `a2a_notify_job_board` for one-way notifications) and consumed by
`AgentWorker` as a persistent actor effect — never through HTTP, webhooks,
or external signature protocols. Proactive is a runtime worker, not a
configured channel, and always starts.

The shared lifecycle primitives (`start`/`stop`, `health()`, `call()` for
blocking BUS calls, `spawn()` for owned child tasks) live in
`magi.runtime_worker.RuntimeWorker`.

## Worker flow

```text
ingress -> Book write + Job Board publish -> worker claim -> external effect
        -> Job Board submit_result / Book update -> client replay or delivery
```

Durable runtime rules (enforced by the architecture guard):

1. Persist input and publish a durable job before a worker performs external
   work.
2. A claim has explicit lease/attempt semantics; consumers are idempotent
   and must tolerate at-least-once delivery.
3. LLM, tool, HTTP, and channel I/O happen outside database transactions.
4. Worker completion is written back through the corresponding Job Board.
5. A terminal committed result outranks a streamed delta.
6. `/api/chat/send` is asynchronous: it returns `202 Accepted` with a
   `job_id` (the `chat_notify_jobs` auto-incrementing primary key) and the `conversation_id`; clients
   consume progress and final state through the durable job / SSE path.

## Important paths

| Path | Responsibility |
| --- | --- |
| `magi/bus/bootstrap.py` | `Bus` dataclass + `open_bus(...)` composition |
| `magi/bus/bases/` | Job/Book/Stream bases (`BaseJobBoard`, `BaseBook`, `BaseFileBook`, `StreamHub`) |
| `magi/bus/bases/db/` | SQLAlchemy `Base`, engine factories, `FileShelf` — no tables |
| `magi/bus/firmwares/schema.py` | Scope-filtered `create_all` + Alembic upgrade |
| `magi/bus/firmwares/alembic/` | Revisioned DDL for firmware tables |
| `magi/bus/firmwares/jobs/` | Concrete Job Boards (`publish → claim → submit_result`) |
| `magi/bus/firmwares/books/local/` | Local-SQLite Books (conversations, tasks, contacts, memory, …) |
| `magi/bus/firmwares/books/magis/` | MAGIS-side Books (society, members, roles, control plane) |
| `magi/bus/firmwares/books/file/` | File-backed `PromptBook` + `SkillsBook` |
| `magi/bus/bases/stream.py` | `StreamHub` — ephemeral SSE notification only |
| `magi/startup/runtime.py` | composition root + worker lifecycle |
| `magi/startup/workers.py` | `WorkerRegistry` — sole owner of all Worker instances |
| `magi/startup/worker.py` | `RuntimeWorker` — shared lifecycle primitives |
| `magi/agent/worker.py` | durable agent-turn consumer (chat loop + A2A receiver) |
| `magi/tools/worker.py` | durable tool-effect consumer |
| `magi/tools/comms/` | A2A `message_magi` tool (persistent actor effect, not delegated to ToolsWorker) |
| `magi/providers/worker.py` | durable LLM-job consumer |
| `magi/mcp/worker.py` | sole MCP connection lifecycle owner |
| `magi/channels/worker_base.py` | `ChannelWorker` — shared outbound-delivery template |
| `magi/channels/{tg,webui,tasks}/worker.py` | per-channel Worker implementations |
| `magi/channels/api/app.py` | FastAPI app factory (Runtime, Control, standalone) |
| `magi/bus/firmwares/books/magis/membershipBook.py` | `MagisMembershipBook.responsibility` + collaboration directory |
| `magi/proactive/worker.py` | system-level proactive policies (last to start) |
| `magi/connectors/` | long-lived external data sources + in-process event bus |
| `magi/new_bus/` | Next MAGI-BUS vNext iteration: next-generation protocol backplane under active integration |

The current runtime and the next MAGI-BUS vNext iteration coexist during the
migration: `magi.bus` is the live, evolved form of the previous new BUS, while
`magi.new_bus` is the next BUS implementation being validated independently.
Do not treat either package as a compatibility alias for the other.

## Channel egress — `ChannelWorker` template

Every *human-facing* channel (Telegram, WebUI, task) implements the same
shape via `magi.channels.worker_base.ChannelWorker`. **A2A is not a channel
worker** — it is the internal MAGIS-internal collaboration path consumed by
`AgentWorker` (see [A2A — MAGIS collaboration](#a2a--magis-collaboration)).

For channels that do follow this template:

- Constructor injection of `Bus` and a `poll_seconds` interval.
- `worker_name = self.channel_name`; a class-level literal (`"tg"` /
  `"webui"`) declares the channel tag.
- `start()` / `stop()` lifecycle inherited from `RuntimeWorker`.
- `_claim_delivery_loop(deliver_fn, channel_label)` — a template method
  that does:

  1. **Backpressure** — read `delivery_job_board.pending_count(channel=…)`
     and compare against `settings_book["channels.delivery.max_queue_depth"]`
     (default 1000); when exceeded, log once per channel per minute and
     sleep `5 × poll_seconds` before retrying.
  2. **Claim** — `bus.delivery_job_board.claim_for_channel(channel, worker_id)`.
  3. **Lease ownership** — the durable row records `worker_id`; a stale worker
     cannot submit a result after another worker reclaims the lease.
  4. **Deliver** — invoke the caller-supplied `_deliver_<channel>` function.
  5. **Submit** — write `DeliveryResult(success, error)` back to the board.

Each outbound worker reduces to a `_deliver_<channel>` coroutine:

| Worker | Delivery effect |
| --- | --- |
| `TelegramWorker._deliver_tg` | raw HTTP via `channels.telegram.bot.send_text_raw(token, chat_id, text)` |
| `WebUIWorker._deliver_webui` | append `assistant` row via `bus.messages_book.add` |
| `TaskWorker` (no channel loop) | publishes a `ChatNotifyJob` after `_fire_task`; the `AgentWorker` does the work and the channel-worker path delivers the reply |

The shared `ChannelWorker` template means per-channel workers never
backpressure independently — depth is global per `channel` filter — and
never retry themselves. An expired lease simply makes the job available to a
later worker; only a worker may submit a business `FAILED` result.

## Bus facade

`Bus` is a frozen dataclass assembled by `open_bus(...)`. It exposes two
API surfaces — **Books** (typed CRUD, return DTOs) and **Job Boards**
(`publish → claim → submit_result` / `get_result`) — plus a `StreamHub`
for ephemeral SSE notifications and a `PromptBook` + `SkillsBook` for
file-backed reads.

```text
Bus (magi/bus/bootstrap.py)
├─ local (always present)
│  ├─ Books
│  │   conversations_book     ConversationBook
│  │   messages_book          MessageBook
│  │   memory_book            MemoryBook
│  │   contacts_book          ContactBook
│  │   contact_notes_book     ContactNoteBook
│  │   settings_book          SettingBook (incl. provider + system config)
│  │   tasks_book             TaskBook (user + preset, source discriminator)
│  │   task_runs_book         TaskRunBook
│  │   tool_definitions_book  ToolDefinitionBook
│  │   tool_catalog_book      ToolCatalogStateBook
│  │   mcp_servers_book       McpServerBook
│  │   token_usage_book       TokenUsageBook
│  │   action_items_book      ActionItemBook
│  │   hook_signoffs_book     HookSignoffBook
│  │   prompt_book            PromptBook (file-backed, always populated)
│  │   skills_book            SkillsBook (file-backed; None if absent)
│  ├─ Job Boards
│  │   agent_job_board        chatNotifyBoard        (ChatNotifyJob in/out)
│  │   tool_job_board         runToolJobBoard     (RunToolJob in/out)
│  │   llm_job_board          callLLMJobBoard     (CallLLMJob in/out)
│  │   delivery_job_board     deliveryJobBoard    (DeliveryJob out)
│  │   change_mcp_server_job_board  changeMCPServerJobBoard
│  │   change_provider_config_job_board  changeProviderConfigJobBoard
│  │   seed_preset_tasks_job_board     seedPresetTasksJobBoard
│  │   run_task_job_board     runTaskJobBoard     (RunTaskJob trigger)
│  └─ StreamHub
│
│  MAGIS shared job boards
│  ├─ a2a_request_job_board   a2aRequestJobBoard  (one request, one response)
│  └─ a2a_notify_job_board    a2aNotifyBoard      (durable one-way notify)
│      stream_hub             StreamHub (in-process SSE only)
└─ magis (None unless MAGIS database configured)
   ├─ magis_book              MagisBook (society tree)
   ├─ magis_admins_book       MagisAdminBook
   ├─ memberships_book        MagisMembershipBook + instruction_context + responsibility + collaboration_directory
   ├─ roles_book              MagisRoleBook (ADAM/EVA reserved)
   ├─ eva_runtimes_book       EvaRuntimeBook
   ├─ control_runtimes_book   ControlRuntimeBook
   ├─ control_secrets_book    ControlSecretBook
   ├─ port_allocations_book   PortAllocationBook
   ├─ workspace_archives_book WorkspaceArchiveBook
```

All Book/Job imports are **lazy** inside `_open_with_dirs`; merely
importing `magi.bus` does not register ORM tables. The runtime never
opens SQLAlchemy sessions itself — domain code consumes the Books / Job
Boards above.

## Domain modules

| Module | Owns | Depends on |
| --- | --- | --- |
| `magi.bus` | `Bus`, Books, Job Boards, StreamHub, file-backed prompt/skill shelves | SQLAlchemy, drivers, filesystem |
| `magi.startup` | path resolution, composition root, Worker lifecycle | `magi.bus`, Worker entry points |
| `magi.agent` | agent turn loop, system prompt, context loading, compaction | `magi.bus` |
| `magi.tools` | tool contracts, registry, durable tool execution | `magi.bus` |
| `magi.providers` | provider adapters and durable LLM-job consumer | `magi.bus` |
| `magi.mcp` | MCP connection lifecycle, `ChangeMCPServerJob` glue | `magi.bus`, `magi.tools` |
| `magi.channels` | HTTP, WebUI, Telegram, task adapters | `magi.bus` |
| `magi.proactive` | system-level proactive policies + Worker | `magi.bus` |

Dependency direction is enforced one-way: `magi.{agent,channels,tools,mcp,
providers,proactive} → magi.bus`. Domain code must never import
`magi.bus.bases.db` (tests/architecture/test_import_boundaries.py).

### `magi.agent` — AgentWorker

- Fair sequential consumer of local `agent_job_board` and the MAGIS-shared
  A2A request/notify boards addressed to its own `magi_id`. A single
  `claim_next_turn()` round-robins across the three sources with per-source
  consecutive-consumption caps so a busy A2A stream cannot starve the local
  chat loop and vice versa.
- Receives a fully-wired `Bus` and the runtime's `magi_id` via constructor
  injection (`AgentWorker(bus, magi_id=…)`). `magi_id` is used to render the
  per-MAGI instruction block via `magi.bus.firmwares.books.magis.membershipBook
  .MagisMembershipBook.instruction_context` and to scope the
  **MAGIS collaboration directory** the LLM sees at every turn
  (`MagisMembershipBook.list_collaboration_directory(magi_id=…)`, filtered to
  the current MAGIS only).
- Loops claim → context assembly → `llm_job_board.publish(CallLLMJob)` →
  wait-for-result → tool dispatch (`tool_job_board` / A2A `message_magi`
  with `mode ∈ {notify, request}`) → `_gather_all`. Human turns publish via
  `delivery_job_board`; A2A `notify` calls `ack()` and never produces an
  outbound A2A message; A2A `request` records **exactly one** response via
  compare-and-set and ends the run. Responses are not new inbound messages
  and cannot themselves carry `request` / `expect_reply` semantics, so
  two Agents never auto-loop just because each wants to "answer the other".
- Steering is in-band: the active loop pulls a fresh same-conversation job
  via `agent_job_board.claim_for_steering(...)` during `_gather_all`.
- Module-private helpers (`agent_context.build_messages_from_session`,
  `system_prompt.build_system_prompt`, `auto_title.request_session_title`,
  `token_usage.record_token_usage`) keep the Worker thin.
- A2A input runs carry an internal-only `RunContext.channel` (`a2a.notify`
  or `a2a.request`) so terminal text is never routed to a human channel and
  the response path is enforced by the runtime rather than by prompt
  heuristics.

### `magi.tools` — ToolsWorker

- Durable consumer of `tool_job_board`.
- `start()` publishes the full tool catalog to `tool_definitions_book` and
  subscribes to `tools.registry.register_tools`'s change events; any later
  injection (MCP, skills) triggers an automatic re-publish.
- Concurrency is bounded by an `asyncio.Semaphore` (default 2), injected
  via the constructor.
- Catalog-revision check on claim prevents stale-schema tool calls.

### `magi.providers` — ProvidersWorker

- Durable consumer of `llm_job_board` (`callLLMJobBoard`).
- Resolves credentials through `magi.providers.factory.get_provider(bus=…)`,
  which reads `provider.name` / `provider.api_key` / `provider.model` from
  the local `settings_book` (the per-MAGI fields that used to live on the
  removed `magic` row).
- Known providers: `claude` / `minimax-cn` / `minimax-global` / `openai`.
  Unknown names raise `LLMError`; missing credentials raise
  `LLMNotConfiguredError`.

### `magi.mcp` — McpWorker

- The single lifecycle owner of every MCP connection in one MAGI process.
- Reads from `mcp_servers_book`, writes back via
  `change_mcp_server_job_board` (the LLM-side manage tools publish to it
  and wait for the result).
- Discovered tools are injected via `tools.registry.register_tools("mcp",
  …)`; the `ToolsWorker` re-publishes its catalog automatically on the
  change event.

### `magi.channels` — ingress + egress

- **Ingress** writes to `conversations_book` / `messages_book` and publishes a
  `ChatNotifyJob` to `agent_job_board`. Telegram is a long-polling inbound worker
  (`python-telegram-bot` v21+ `Application.start_polling`); the WebUI
  ingress is the FastAPI route `POST /api/chat/send`. A2A is **not** an HTTP
  ingress: peer `message_magi` tool effects persist directly into the
  MAGIS-shared `a2a_request_job_board` / `a2a_notify_job_board` and are
  claimed by `AgentWorker` itself (see [A2A — MAGIS collaboration](#a2a--magis-collaboration)).
- **Egress** is the `ChannelWorker._claim_delivery_loop` template above.
  Each channel worker adds its own `_run` that calls the template with a
  `_deliver_<channel>` function.
- The HTTP API factory (`create_runtime_app` / `create_control_app` /
  `create_app`) mounts the channel-agnostic feature routers (auth,
  onboarding, chat conversations, contacts, memory, tasks, MCP, skills, …)
  plus the per-channel delivery surface.

## A2A — MAGIS collaboration

The A2A surface is how MAGI of the same Society collaborate. It is not an
HTTP route, not a webhook, and not a signed external protocol: messages
are persisted into the shared MAGIS database and consumed by
`AgentWorker` as a persistent actor effect.

### Two terminal modes, never an open conversation

The old `expect_reply: bool` switch could not express correct loop
termination and was replaced by two explicit, terminal message modes:

| Mode | Sender semantics | Receiver terminal state | Auto-reply? |
| --- | --- | --- | --- |
| `notify` | "Here is a fact / progress / reminder" — no business answer expected | Agent processes and acknowledges consumption | No |
| `request` | "Please answer this one question / delegation" | Agent's final text is written as the request's unique response | Yes, once, only back to the original request |

Responses are **not** new inbound messages and carry no `request` /
`expect_reply` semantics, so two Agents never enter a free-running
  "answer-the-other" loop. A receiver that genuinely needs to reach back
to the sender must call `message_magi` itself with a fresh `notify` or
`request`. Each `request` admits at most one response; late responses do
not silently re-awaken a sender that already terminated.

### BUS data model

The boards are instantiated through `Bus._magis_factory` and never write
to a MAGI's local SQLite:

- `A2ARequestJobBoard` extends `BaseJobBoard` (publish → CAS claim →
  lease → retry → `submit_result` / `get_result`) with a
  `claim_for_target(magi_id=…)` so only the addressed MAGI can claim.
  Request rows carry `source_magi_id`, `target_magi_id` (both FKs to
  `magis_memberships.id`), `conversation_id` (tracking only, no
  auto-reply semantics), `text` / JSON payload, `deadline_at`,
  lease, attempt, timestamps, and a `response_status` /
  `response_payload` pair (`pending` / `responded` / `rejected` /
  `timed_out` / `failed`) that may be written **exactly once**.
  "Target claimed" and "request has a business response" are deliberately
  separated, unlike the previous `sendA2AJob` path.
- `A2ANotifyBoard` reuses `BaseNotifyBoard` as the sender's
  fire-and-forget base — successful `publish()` means "persisted", with
  no sender-side result polling. The concrete board additionally
  implements the receiver-side primitives `claim_for_target(magi_id=…)`,
  `ack()`, retry on failure, and expiration handling. These primitives
  live on the A2A board until a second consumer-of-notifications proves
  the need for a shared base class.
- Both tables carry a `(target_magi_id, status)` claim index and a
  unique idempotency index for the sender's effect.
  Pre-insert validation in the Book/board enforces that `source` and
  `target` belong to the same MAGIS and that a MAGI cannot send to itself.

### `message_magi` tool

The single persistent-actor-effect tool for A2A lives in
`magi/tools/comms/` and is recognised by `AgentWorker` as a durable
actor effect (it is never delegated to `ToolsWorker`):

```json
{
  "magi_id": 42,
  "mode": "notify | request",
  "text": "...",
  "deadline_seconds": 120
}
```

- `magi_id` must come from the collaboration directory; the worker
  re-validates same-MAGIS and not-self before persisting.
- A `notify` tool result is "persisted" and never enters a wait set.
- A `request` joins the existing gather flow until it gets the unique
  response, fails, or hits its `deadline_seconds`; the response arrives
  as the matching `tool_use_id`'s `tool_result` in the current Agent
  reasoning.
- The old `expect_reply` switch, the model-controlled `reply_to`, and
  HTTP address parameters are gone.

### MAGIS collaboration directory

`magis_memberships.role` is an org classification, not a directory
entry. Each membership now carries a public, editable
`responsibility: Text` describing the MAGI's current scope,
specialisations, boundaries, and expected deliverables
(e.g. "owns the WebUI frontend, React Query, and build verification;
does not run database migrations"). It does not replace the role
instruction and is never stored in a MAGI's private settings.

The directory is exposed by
`MagisMembershipBook.list_collaboration_directory(magi_id=…)` and
returns only the public directory for the caller's MAGIS. Each entry
contains `magi_id`, the public runtime name (resolved via
`runtime_state_book.backend_ref`, defaulting to `MAGI #id`),
`role_name`, and `responsibility`. Private prompts, API keys, memory,
and conversation contents never leak into the directory.

`AgentWorker` reads the directory on every turn and renders it as a
compact "MAGIS collaboration directory" block in the system prompt, with
the self entry highlighted so the model can pick collaborators
correctly before calling a tool. Updates to `responsibility` or role
take effect without restarting the Agent.

The WebUI MAGIS membership create/update models and serializers carry
`responsibility` as well. The field is treated as public collaboration
metadata maintained by MAGIS operators, never as a free-form LLM
setting.

### `magi.proactive` — ProactiveWorker

- The **last** Worker started; it never blocks the runtime composition root
  (it bootstraps after the dependency-ordered pool is up).
- `_bootstrap()` runs once at start: if this MAGI is its parent MAGIS's
  ADAM (`magi_book.get(magis_id).adam_id == self._magi_id`), idempotently
  inserts a credentials-nudge ActionItem for every MAGIS admin via
  `magi.proactive.credentials_action.ensure_for_admin`.
- Main loop drains `seed_preset_tasks_job_board` via
  `magi.proactive.preset_tasks.handle_seed_job`.

## Storage ownership

| Scope | Location | Book(s) |
| --- | --- | --- |
| **MAGI private** | `<workspace>/memories/magi.db` (SQLite) | All `magi.bus.firmwares.books.local.*` Books (conversations, messages, memory, contacts, settings, tasks, tool catalog state, durable local job boards, delivery state) |
| **MAGIS shared** | `MAGIS_DATABASE_URL`, or `MAGI_Societies/<magis-name>/magis.db` for named local SQLite | `magi.bus.firmwares.books.magis.*` Books (society tree, admins, memberships, roles, runtime-control records, singleton WebUI control settings) **and** the MAGIS-shared A2A request/notify job boards (instantiated via `Bus._magis_factory`, never written to a MAGI's local SQLite) |
| **File-backed** | `<workspace>/prompts/<owner>/…`, `<workspace>/skills/<name>/…` | `PromptBook`, `SkillsBook` |

`magi.bus` owns the engine factories, table registration, and file-backed
shelves for both scopes. No other package opens either database directly
(architecture guard `test_import_boundaries.py`). The runtime never uses a
MAGI's private SQLite as a substitute for shared MAGIS state —
`open_bus` raises `ValueError` when the local and MAGIS URLs coincide,
so a single database cannot accidentally serve both scopes.

`MAGIS_NAME` is a lowercase slug (`[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?`,
enforced by `magi.startup.paths._magis_storage_name`) that defaults to
`genesis`. When `MAGIS_DATABASE_URL` is unset, the bootstrap selects
`<host>/MAGI_Societies/<slug>/magis.db` for that slug; SQLite URLs
therefore identify a per-MAGIS file. When `MAGIS_DATABASE_URL` is set, it
is authoritative — PostgreSQL URLs identify a distinct database (e.g.
`magis_42`) on a shared service. The BUS does not infer or silently reuse
another MAGIS's URL.

Package assets are first-run defaults, not a runtime read path. Each Worker
seeds only the prompt records it owns through `PromptBook.ensure(...)` during
its startup: `AgentWorker` owns `prompts/agent/`, and `ProactiveWorker` owns
`prompts/proactive/`. Existing workspace records are never overwritten.
Skills have no Worker owner, so `build_default_skills_book()` seeds missing
package skill directories into `<workspace>/skills/` when the node BUS opens;
afterward `SkillsBook` reads only that workspace directory.

Schema materialisation is scoped: `synchronise_schema(local_factory,
scope=LOCAL_SCOPE)` creates local Books and durable local job boards only
in the MAGI-private store, while `synchronise_schema(magis_factory,
scope=MAGIS_SCOPE)` creates `firmwares.books.magis` Books and the A2A boards only
in the MAGIS store. Two distinct DSNs therefore cannot accidentally
receive each other's tables.

MAGIS rows never carry SQL foreign keys into local tables. MAGIS-level
records (admins, credentials, memberships) keep their contact association
as an opaque identity value that the BUS / API layer validates, so the
same schema is valid against both a remote PostgreSQL MAGIS and an
isolated local SQLite MAGIS.

Schema changes are explicit BUS migrations; the runtime uses one schema
and one implementation, without fallback reads, compatibility imports,
or dual writes.

## Verification

The architecture guard in `tests/architecture/test_import_boundaries.py`
enforces:

- Domain code does not import `magi.bus.bases.db`.
- `magi/bus/bases/` does not import firmwares. Table and column definitions live only in `magi/bus/firmwares/`.
- BUS does not import domain worker implementations.
- `magi.new_bus` is MAGI-BUS vNext, not a retired import path. Its adoption by
  the runtime must be an explicit composition-root migration rather than an
  implicit compatibility shim.

The hook subsystem has its own guard tests (`test_hook_import_boundaries.py`
and `test_hook_envelope_purity.py`).

## Further reading

- [MAGI terms]({{ '/terms/' | relative_url }}) — vocabulary and the canonical ID names.
- [Business flows]({{ '/business-flows/' | relative_url }}) — invariant behaviour and guard
  conditions for the chat loop, channels, tasks, onboarding, login, and
  tools.
- [Roadmap]({{ '/roadmap/' | relative_url }}) — the forward-looking backlog.

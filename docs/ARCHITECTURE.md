# MAGI Architecture

The current MAGI runtime is organised around one durable boundary — **BUS**
(`magi.bus`) — that owns Books (typed CRUD), Job Boards (publish → claim →
submit_result), and the file-backed prompt/skill shelves. This document is
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
| Composition-root entry | `open_bus(state_dir=…, magis_url=…)` |
| Control-plane entry | `open_control_bus(control_dir=…, magis_url=…)` |
| Durable CRUD / query API | **Book** (e.g. `sessions_book`, `messages_book`) |
| Durable `publish → claim → submit_result` API | **Job Board** (e.g. `agent_job_board`) |
| Ephemeral notification aid | `stream_hub` (not a source of truth) |

There is no second bus, compatibility package, fallback singleton,
dual-write path, or alternate BUS implementation. The retired
`magi.new_bus` / `NewBus` / `bootstrap_new_bus` names are banned by
`tests/architecture/test_import_boundaries.py`.

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

A single process owns one `Bus`; the Bus facade is built by
`magi.bus.bootstrap.open_bus(...)` and injected (by constructor) into every
Worker. There is no process-global `BUS` singleton and no alternate Bus
implementation — the import-boundary tests in `tests/architecture/` enforce
this.

## Composition root

`magi.startup.runtime.run_magi` is the single composition root for one MAGI
process. It:

1. Reads and validates the provisioned `RuntimeSpec` (`magi.startup.spec`).
2. Builds the `StartupContext` (paths, MAGI name, MAGI identity, database
   URLs, runtime port).
3. Opens one `Bus` via `open_bus(state_dir=…, magis_url=…)`.
4. Validates the runtime identity against the provisioned MAGIS records
   (`memberships_book`, `control_runtimes_book`, `port_allocations_book`).
5. Constructs a `WorkerRegistry(bus, …)` — see
   `magi.startup.workers.WorkerRegistry`.
6. Starts the workers in dependency order.
7. Serves the private Runtime FastAPI app (`create_runtime_app`) on the
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
 ├─ a2a         — A2AWorker                 (always)
 └─ proactive   — ProactiveWorker           (always)
```

Channel workers are conditional: Telegram and TaskWorkers start only when
`bus.settings_book["channels.enabled"]` lists the channel. WebUI and A2A are
required runtime capabilities and are always added by the composition root
(the persisted and fallback default is `["webui"]`). Proactive is a runtime
worker, not a configured channel, and always starts.

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
6. `/api/chat/send` is asynchronous: it returns `202 Accepted` with a `run_id`
   (job id); clients consume progress and final state through the durable
   run / SSE path.

## Important paths

| Path | Responsibility |
| --- | --- |
| `magi/bus/bootstrap.py` | `Bus` dataclass + `open_bus(...)` composition |
| `magi/bus/db/` | SQLAlchemy `Base`, engine factories, `FileShelf` (private) |
| `magi/bus/guild/` | Job Boards (`BaseJobBoard`, `publish → claim → submit_result`) |
| `magi/bus/library/local/` | Local-SQLite Books (conversations, tasks, contacts, memory, …) |
| `magi/bus/library/magis/` | MAGIS-side Books (society, members, roles, control plane) |
| `magi/bus/library/file/` | File-backed `PromptBook` + `SkillsBook` |
| `magi/bus/stream.py` | `StreamHub` — ephemeral SSE notification only |
| `magi/startup/runtime.py` | composition root + worker lifecycle |
| `magi/startup/workers.py` | `WorkerRegistry` — sole owner of all Worker instances |
| `magi/startup/worker.py` | `RuntimeWorker` — shared lifecycle primitives |
| `magi/agent/worker.py` | durable agent-turn consumer (chat loop) |
| `magi/tools/worker.py` | durable tool-effect consumer |
| `magi/providers/worker.py` | durable LLM-job consumer |
| `magi/mcp/worker.py` | sole MCP connection lifecycle owner |
| `magi/channels/worker_base.py` | `ChannelWorker` — shared outbound-delivery template |
| `magi/channels/{tg,webui,tasks,a2a}/worker.py` | per-channel Worker implementations |
| `magi/channels/api/app.py` | FastAPI app factory (Runtime, Control, standalone) |
| `magi/proactive/worker.py` | system-level proactive policies (last to start) |
| `magi/connectors/` | long-lived external data sources + in-process event bus |

There is no alternate BUS implementation or compatibility import path; the
retired `magi.new_bus` / `NewBus` / `bootstrap_new_bus` names are
forbidden by `tests/architecture/test_import_boundaries.py`.

## Channel egress — `ChannelWorker` template

Every channel (Telegram, WebUI, A2A, task) implements the same shape via
`magi.channels.worker_base.ChannelWorker`:

- Constructor injection of `Bus` and a `poll_seconds` interval.
- `worker_name = self.channel_name`; a class-level literal (`"tg"` /
  `"webui"` / `"a2a"`) declares the channel tag.
- `start()` / `stop()` lifecycle inherited from `RuntimeWorker`.
- `_claim_delivery_loop(deliver_fn, channel_label)` — a template method
  that does:

  1. **Backpressure** — read `delivery_job_board.pending_count(channel=…)`
     and compare against `settings_book["channels.delivery.max_queue_depth"]`
     (default 1000); when exceeded, log once per channel per minute and
     sleep `5 × poll_seconds` before retrying.
  2. **Claim** — `bus.delivery_job_board.claim()`.
  3. **Release-if-mismatched** — if `job.channel != channel_label`, release
     the job back to the board and let the matching worker claim it.
  4. **Deliver** — invoke the caller-supplied `_deliver_<channel>` function.
  5. **Submit** — write `DeliveryResult(success, error)` back to the board.

Each outbound worker reduces to a `_deliver_<channel>` coroutine:

| Worker | Delivery effect |
| --- | --- |
| `TelegramWorker._deliver_tg` | raw HTTP via `channels.telegram.bot.send_text_raw(token, chat_id, text)` |
| `WebUIWorker._deliver_webui` | append `assistant` row via `bus.messages_book.add` |
| `A2AWorker._deliver_a2a` | peer HTTP via `channels.a2a.transport.send_a2a_delivery` |
| `TaskWorker` (no channel loop) | publishes a `ChatJob` after `_fire_task`; the `AgentWorker` does the work and the channel-worker path delivers the reply |

The shared `ChannelWorker` template means per-channel workers never
backpressure independently — depth is global per `channel` filter — and
never retry themselves. Retry is owned by `BaseJobBoard._claim` (lease
expiry → re-lease up to `MAX_ATTEMPTS=3` → mark exhausted).

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
│  │   sessions_book          ConversationBook + MessageBook
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
│  │   agent_job_board        chatJobBoard        (ChatJob in/out)
│  │   tool_job_board         runToolJobBoard     (RunToolJob in/out)
│  │   llm_job_board          callLLMJobBoard     (CallLLMJob in/out)
│  │   delivery_job_board     deliveryJobBoard    (DeliveryJob out)
│  │   a2a_job_board          sendA2AJobBoard     (SendA2AJob out)
│  │   mcp_server_changed_job_board  mcpServerChangedJobBoard
│  │   change_provider_config_job_board  changeProviderConfigJobBoard
│  │   seed_preset_tasks_job_board     seedPresetTasksJobBoard
│  │   run_task_job_board     runTaskJobBoard     (RunTaskJob trigger)
│  └─ StreamHub
│      stream_hub             StreamHub (in-process SSE only)
└─ magis (None unless MAGIS database configured)
   ├─ magis_book              MagisBook (society tree)
   ├─ magis_admins_book       MagisAdminBook
   ├─ memberships_book        MagisMembershipBook + instruction_context
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
| `magi.mcp` | MCP connection lifecycle, `McpServerChangedJob` glue | `magi.bus`, `magi.tools` |
| `magi.channels` | HTTP, WebUI, Telegram, task, A2A adapters | `magi.bus` |
| `magi.proactive` | system-level proactive policies + Worker | `magi.bus` |
| `magi.connectors` | long-lived external data sources, in-process event bus | `magi.bus` (configs) |

Dependency direction is enforced one-way: `magi.{agent,channels,tools,mcp,
providers,proactive,connectors} → magi.bus`. Domain code must never import
`magi.bus.db` (tests/architecture/test_import_boundaries.py).

### `magi.agent` — AgentWorker

- Sequential consumer of `agent_job_board` (chatJobBoard).
- Receives a fully-wired `Bus` and the runtime's `magi_id` via constructor
  injection (`AgentWorker(bus, magi_id=…)`). `magi_id` is used to render the
  per-MAGI instruction block via `magi.bus.library.magis.membershipBook
  .MagisMembershipBook.instruction_context`.
- Loops claim → context assembly → `llm_job_board.publish(CallLLMJob)` →
  wait-for-result → tool dispatch (`tool_job_board` / `a2a_job_board`) →
  `_gather_all` → publish reply via `delivery_job_board`.
- Steering is in-band: when a fresh `ChatJob` for an already-active
  conversation arrives, the Worker releases it back to the board; the
  active loop pulls it as steering via
  `agent_job_board.claim_for_conversation(...)` during `_gather_all`.
- Module-private helpers (`agent_context.build_messages_from_session`,
  `system_prompt.build_system_prompt`, `auto_title.request_session_title`,
  `token_usage.record_token_usage`) keep the Worker thin.

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
  `mcp_server_changed_job_board` (the LLM-side manage tools publish to it
  and wait for the result).
- Discovered tools are injected via `tools.registry.register_tools("mcp",
  …)`; the `ToolsWorker` re-publishes its catalog automatically on the
  change event.

### `magi.channels` — ingress + egress

- **Ingress** writes to `sessions_book` / `messages_book` and publishes a
  `ChatJob` to `agent_job_board`. Telegram is a long-polling inbound worker
  (`python-telegram-bot` v21+ `Application.start_polling`); the WebUI
  ingress is the FastAPI route `POST /api/chat/send`. A2A peer ingress is
  the `a2a_job_board` plus the `a2a_router` FastAPI route.
- **Egress** is the `ChannelWorker._claim_delivery_loop` template above.
  Each channel worker adds its own `_run` that calls the template with a
  `_deliver_<channel>` function.
- The HTTP API factory (`create_runtime_app` / `create_control_app` /
  `create_app`) mounts the channel-agnostic feature routers (auth,
  onboarding, chat sessions, contacts, memory, tasks, MCP, skills, …)
  plus the per-channel delivery surface.

### `magi.proactive` — ProactiveWorker

- The **last** Worker started; it never blocks the runtime composition root
  (it bootstraps after the dependency-ordered pool is up).
- `_bootstrap()` runs once at start: if this MAGI is its parent MAGIS's
  ADAM (`magi_book.get(magis_id).adam_id == self._magi_id`), idempotently
  inserts a credentials-nudge ActionItem for every MAGIS admin via
  `magi.proactive.credentials_action.ensure_for_admin`.
- Main loop drains `seed_preset_tasks_job_board` via
  `magi.proactive.preset_tasks.handle_seed_job`.

### `magi.connectors` — external data streams

- Long-lived objects with `connect()` / `disconnect()` / `fetch()` /
  `name()` / `config()` (see `magi.connectors.base.Connector`).
- Lifecycle: operator adds a `ConnectorConfig` row; the runtime calls
  `connectors.registry.load_connectors()` at boot, which constructs one
  connector per enabled config and calls `await connector.connect()`.
- Emits `ConnectorEvent`s into an in-process pub/sub bus
  (`magi.connectors.bus.EventBus`) — shared with the plugins subsystem.
  The bus is in-process only; cross-MAGI event sharing would route via
  a2a (deferred).
- The LLM never calls a connector directly; tool wrappers call
  `connector.fetch(...)` (Gmail, Calendar, Linear, …).

## Storage ownership

| Scope | Location | Book(s) |
| --- | --- | --- |
| **MAGI private** | `<workspace>/memories/magi.db` (SQLite) | All `magi.bus.library.local.*` Books |
| **MAGIS shared** | `MAGIS_DATABASE_URL`, or `MAGI_Societies/<magis-name>/magis.db` for named local SQLite | `magi.bus.library.magis.*` Books |
| **File-backed** | `magi/prompts/`, `<workspace>/skills/` | `PromptBook`, `SkillsBook` |

`magi.bus` owns the engine factories, table registration, and file-backed
shelves for both scopes. No other package opens either database directly.
SQLite MAGIS stores are isolated by `MAGIS_NAME`; PostgreSQL deployments use
one service with one distinct database per MAGIS. BUS provisioning creates
only the tables owned by the selected scope.
Schema changes are explicit BUS migrations; the runtime uses one schema
and one implementation, without fallback reads, compatibility imports, or
dual writes. See [MAGI and MAGIS Storage](magi-magis-storage.md) and
[Production Persistence](production-persistence.md).

## Verification

The architecture guard in `tests/architecture/test_import_boundaries.py`
enforces:

- Domain code does not import `magi.bus.db`.
- BUS does not import domain worker implementations.
- The retired `magi.new_bus` / `NewBus` / `bootstrap_new_bus` names never
  reappear in production code.

The hook subsystem has its own guard tests (`test_hook_import_boundaries.py`
and `test_hook_envelope_purity.py`).

## Further reading

- [MAGI terms](terms.md) — vocabulary.
- [Unified WebUI and Runtime API](unified-webui.md) — browser-facing
  WebUI ↔ Runtime proxy contract.
- [Business flows](business-flows.md) — invariant behaviour and guard
  conditions for the chat loop, channels, tasks, onboarding, login, and
  tools.
- [MAGI and MAGIS storage](magi-magis-storage.md),
  [Production persistence](production-persistence.md) — storage
  boundaries.
- [ID naming standard](design/id-naming-standard.md) — the migration
  table for the `magic_id → magi_id` / `uid → contact_id` /
  `session_id → conversation_id` rename.

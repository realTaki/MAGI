# MAGI Terms

| Term | Meaning |
| --- | --- |
| **MAGI** | Modular Agentic Group Intelligence, the product and runtime family. |
| **MAGIS** | A MAGI Society: an organization containing MAGI runtimes. |
| **MAGIC** | One concrete MAGI runtime process and its private state. |
| **ADAM** | A manager-archetype MAGIC that owns the control-plane experience. |
| **EVA** | A worker-archetype MAGIC that serves an assigned employee or workload. |
| **BUS** | The sole durable application boundary, implemented by `magi.bus`. |
| **Bus** | The process-local BUS facade opened by `open_bus(...)`. |
| **Book** | A BUS API for durable CRUD/query operations that returns DTOs or JSON-safe values. |
| **Job Board** | A BUS API for durable `publish -> claim -> submit_result` workflows. |
| **ChatJob** | Durable agent input from a channel, task, or A2A ingress. |
| **DeliveryJob** | Durable outbound message for a channel worker. |
| **MAGI private SQLite** | Per-runtime state database, normally `<workspace>/memories/magi.db`. |
| **MAGIS database** | Organization-scoped database reached through the configured MAGIS URL. |
| **A2A** | Internal agent-to-agent transport; it is not an authorization system. |

The Python package `magi.bus` owns Books, Job Boards, database factories, and
their ORM implementation. Domain code uses its public contracts and does not
open sessions or expose ORM rows.

## Canonical ID names

One concept, one identifier name — in ORM columns, DTO fields, function
parameters, API payloads, and documentation alike. These are the only
supported names.

| Concept | Canonical ID | Notes |
| --- | --- | --- |
| MAGI instance | `magi_id` | — |
| MAGIS tree | `magis_id` | — |
| Person / contact | `contact_id` | The `contacts` table PK; also the cookie identity. |
| Telegram user | `tgid` | — |
| Telegram chat | `chat_id` | Inside the Telegram channel `tgid == chat_id` for direct chats. |
| Conversation | `conversation_id` | The `chat_conversations` table PK. Never `session_id` — `session` means a SQLAlchemy session. |
| Message | `message_id` | — |
| Job (any Job Board) | `job_id` | Includes agent turns: `ChatJob.job_id` is the `chat_jobs` natural key. |
| Tool call | `tool_call_id` | — |
| Task | `task_id` | — |
| Task run | `run_id` | Task-scoped only (`task_runs`). Agents have no "run" concept — steering keys off `conversation_id`. |
| Memory entry | `memory_id` | — |
| Contact note | `note_id` | — |
| Action item | `action_item_id` | — |
| Runtime | `runtime_id` | — |
| MAGIS role | `role_id` | — |
| Parent MAGIS | `parent_id` | — |
| Adam MAGI | `adam_id` | — |
| Shell session | `bash_id` | — |
| Connector instance | `instance_id` | — |
| Plugin | `plugin_id` | — |
| Hook signoff | `signoff_id` | — |

### Retired names

Old names survive in git history, pre-migration database dumps, and old
cookies. They are not valid in current code or documentation.

| Retired | Canonical | Landed in |
| --- | --- | --- |
| `magic_id` | `magi_id` | code rename (spelling artefact) |
| `uid` | `contact_id` | 7 tables renamed (`chat_conversations`, `chat_messages`, `tasks`, `memory_entries`, `token_usage`, `action_items`, `hook_signoffs`) |
| `session_id` | `conversation_id` | table `chat_sessions` → `chat_conversations`; `sessionBook.py` → `conversationBook.py` |
| `tg_chat_id` | `chat_id` | code rename |
| `event_id` | `job_id` | Alembic `0002_drop_run_id_and_rename_event_id` (`chat_jobs`) |
| `run_id` (agent context) | removed | same revision; agents key off `conversation_id` |

Two renames are agreed but **not yet applied to the code**: `telegram_id` →
`tgid` (still the `contacts` column name and the dominant parameter name),
and the local shorthand `conv_id` → `conversation_id` in
`magi/agent/worker.py`. Document what the code actually says until they land.

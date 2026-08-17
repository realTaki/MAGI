---
title: Terms
description: Shared vocabulary and canonical identifier names for MAGI.
permalink: /terms/
---

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
| **bases** | BUS primitives: Job/Book bases and the database layer (`magi/bus/bases/`). |
| **firmwares** | Concrete Job Boards, Books, and their schema (`magi/bus/firmwares/`). |
| **Book** | A BUS API for durable CRUD/query operations that returns DTOs or JSON-safe values. |
| **Job Board** | A BUS API for durable `publish -> claim -> submit_result` workflows. |
| **ChatNotifyJob** | Durable agent input from a channel, task, or A2A ingress. |
| **DeliveryJob** | Durable outbound message for a channel worker. |
| **MAGI private SQLite** | Per-runtime state database, normally `<workspace>/memories/magi.db`. |
| **MAGIS database** | Organization-scoped database reached through the configured MAGIS URL. |
| **A2A** | Internal agent-to-agent transport; it is not an authorization system. |

The Python package `magi.bus` owns Books, Job Boards, database factories, and
their ORM implementation. Bases (`magi/bus/bases/`) hold the contracts and
storage engines; firmwares (`magi/bus/firmwares/`) hold the concrete
Jobs, Books, and their table/column definitions.
Domain code uses its public contracts and does not open sessions or expose
ORM rows.

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
| Job (any Job Board) | `job_id` | The per-Job-table auto-incrementing primary key; a Board supplies its scope. |
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
| `event_id` | `job_id` | `chat_notify_jobs` rename; the current `job_id` is the table's auto-incrementing primary key |
| `run_id` (agent context) | removed | same revision; agents key off `conversation_id` |
| `telegram_id` | `tgid` | Alembic initial schema (`contacts`); code, API payloads and WebUI renamed with it. Signed-session cookie bumped v3 → v4; proxy header `X-MAGI-Proxy-Telegram-ID` → `X-MAGI-Proxy-Tgid` |
| `conv_id` | `conversation_id` | code rename (`magi/agent/worker.py` local shorthand) |

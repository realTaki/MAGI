# MAGI Architecture

> The design philosophy, component layout, and core mechanics of MAGI.
> For the high-level vision, see the [README](../README.md).
> For the build plan, see [ROADMAP.md](ROADMAP.md).
> For the current production storage boundary and remaining work, see
> [production-persistence.md](production-persistence.md).
> For the BUS-centric durable Actor runtime and module boundaries, see
> [MAGI_BUS_CENTRIC_ARCHITECTURE.md](MAGI_BUS_CENTRIC_ARCHITECTURE.md).

---

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

---

## Repository Layout

```
magi/
├── __main__.py     # sole service entry point (`magi runtime` / `magi webui`)
├── agent/          # The core runtime — what every MAGI runs
│   ├── step.py     # one provider inference step
│   ├── worker.py   # durable inbox consumer and transition owner
│   ├── memory/     # Three-layer memory: session, contacts, self
│   └── llm/        # Provider adapters (Anthropic, Minimax, OpenAI)
├── channels/       # How agents connect to the outside world
│   ├── dispatcher.py   # D.28 — domain code talks to this, never to adapters
│   ├── tasks/          # Scheduled-task CRUD, timing and execution
│   ├── telegram/       # TG bot adapter
│   └── webui/          # FastAPI app + React SPA
├── proactive/      # Proactive policies and task-preset injection
├── prompts/        # Central Markdown + YAML prompt corpus and hot-reload loader
├── tools/          # Capability layer: built-ins, Skills and MCP integration
├── db/             # Shared SQLite, MAGIS PostgreSQL, ORM and Alembic boundary
└── WebUI/          # React 19 + Vite 5 + Tailwind v4 SPA
```

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

---

## Agent Loop

`magi.agent.worker.AgentWorker` consumes durable inputs and invokes
`magi.agent.step.run_agent_step()`:

1. Validate per-agent credentials (mandatory; no fallback)
2. Assemble system prompt (SOUL.md persona + memory + contacts + skills)
3. Load conversation history with auto-compaction
4. Run tool loop — up to N iterations of LLM → tool call → result
5. Interrupt-poll for new user messages between iterations
6. Record one `token_usage` row per LLM call

---

## Persistence

There are two storage domains. A MAGI's private SQLite is for local runtime state;
its one direct MAGIS PostgreSQL database is for organization facts. ADAM's child-tree
management permission does not grant it a second runtime database or public mount.

| Domain | Tables / files | Owner |
|---|---|---|
| Private SQLite + `/workspace` | sessions, memory, contacts, tasks, settings, SOUL, skills | one MAGI |
| MAGIS PostgreSQL + `/magis` | `magis`, `magic`, roles, memberships, instructions, providers, `eve_runtimes` | one MAGIS |

The compatibility Alembic baseline retains historical organization DDL in SQLite,
but new runtime organization reads/writes use `magi.db.magis` and PostgreSQL.

## Private SQLite tables

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

---

## Glossary

- **MAGI** — The general kind of autonomous agent in this system.
- **MAGIS** — A MAGI Society. A group of MAGI that forms a tree via `parent_id`.
- **MAGIC** — Internal table/API name for an individual MAGI; not a separate product term.
- **ADAM** — Leading MAGI role for its direct MAGIS. MAGIS administrator grants
  are direct-only and do not inherit across the society tree.
- **EVA** — Default working MAGI role. Executes tasks and collaborates.
- **Role** — ADAM and EVA are reserved roles; a MAGIS can also define custom roles.
- **Contact** — A person known to the society. Role: `admin` (WebUI operator), `assigned` (the served user), or `guest` (everyone else).

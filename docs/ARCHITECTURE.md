# MAGI Architecture

> The design philosophy, component layout, and core mechanics of MAGI.
> For the high-level vision, see the [README](../README.md).
> For the build plan, see [ROADMAP.md](ROADMAP.md).

---

## Agentic Society Model

MAGI is built around the idea of an **agentic society** — not a single agent, not a
chatbot serving a person, but a **group of autonomous agents that form organizations
and act as a collective**.

The society is composed of three layers:

```
  Society (MAGI)              The whole collective — all agents, all societies, all state
    └── Societies (MAGISes)   Organizations. One leader (Adam) + many members (EVEs).
          └── Citizens (MAGICs) Individual agents. Each has its own container,
                               identity, memory, tools, and LLM.
          └── Contacts         The people known to the society. Operators or recipients.
```

An agent is not a thread. Not a session. **A citizen** — with its own container,
its own identity, its own LLM credentials, and its own persistent state.

---

## Runtime Principle

**One agent = one container = one runtime process.**

There is one binary (`magi`). At boot, `MAGI_NODE_ROLE` selects the archetype preset
(`adam` or `eve`), which determines the Citizen's position in its MAGIS.
Every architectural choice is an independent configuration axis:

| Axis | Env var | Default by archetype |
|---|---|---|
| Position | `MAGI_NODE_ROLE` | `adam` = leader, `eve` = member |
| Channels | `settings.channels.enabled` (DB) | seeded `[webui]`; editable in the UI — not a launch flag |
| State backend | `MAGI_STATE_BACKEND` | `auto` (SQLite) |
| Adam peer | `MAGI_ADAM_URL` | `http://adam:42069` |
| LLM provider | `ANTHROPIC_API_KEY` etc. | per-agent configuration |

---

## Repository Layout

```
magi/
├── agent/          # The core runtime — what every MAGI runs
│   ├── loop.py     # handle_message(): one turn of the agent loop
│   ├── tools/      # Registry + base + 20+ tool implementations
│   ├── memory/     # Three-layer memory: session, contacts, self
│   ├── db/         # SQLAlchemy ORM + settings KV store
│   ├── llm/        # Provider adapters (Anthropic, Minimax, OpenAI)
│   ├── proactive/  # Scheduled task engine
│   └── prompts/    # Markdown + YAML prompt templates (soul.md, memory_block.md, …; bot_replies.yaml)
├── channels/       # How agents connect to the outside world
│   ├── dispatcher.py   # D.28 — domain code talks to this, never to adapters
│   ├── telegram/       # TG bot adapter
│   └── webui/          # FastAPI app + React SPA
├── node/           # Bootstrap: NodeConfig → init → run
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

`magi/agent/loop.py::handle_message()`:

1. Validate per-agent credentials (mandatory; no fallback)
2. Assemble system prompt (SOUL.md persona + memory + contacts + skills)
3. Load conversation history with auto-compaction
4. Run tool loop — up to N iterations of LLM → tool call → result
5. Interrupt-poll for new user messages between iterations
6. Record one `token_usage` row per LLM call

---

## Database (14 tables)

| Table | Holds |
|---|---|
| `contacts` | Person directory (unified `employees` + `contact_entries` + `user_im_bindings`) |
| `magis` | MAGIS tree (via `parent_id`; `adam_id` points to its Adam MAGIC Citizen) |
| `magic` | MAGIC Citizen rows (bound to a `magis`; `adam` / `eve` position) |
| `action_items` | Operator to-do inbox |
| `token_usage` | Per-call LLM billing |
| `tasks` / `task_runs` | Scheduled tasks |
| `chat_sessions` / `chat_messages` | Conversation history |
| `chat_messages_fts` | FTS5 trigram full-text search |
| `memory_entries` | MAGI's self-memory |
| `mcp_servers` | Operator-configured MCP servers (name, type, endpoint, env, headers) |
| `meta` / `settings` | KV runtime config |

SQLite with WAL + `busy_timeout=5000` + `BEGIN IMMEDIATE`.

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

## Glossary

- **MAGI** — The general kind of autonomous agent in this system.
- **MAGIS** — A MAGI Society. A group of MAGIs (one Adam + N EVEs). Forms a tree via `parent_id`. ("MAGI Societies" in operator-facing copy.)
- **MAGIC** — An individual MAGI agent (a citizen of a Society). Container, identity, LLM, tools. ("MAGI Citizens" in operator-facing copy.)
- **Adam** — Leader agent (a MAGIC with `magic_position='adam'`). Manages a MAGIS, dispatches work.
- **EVE** — Member agent (a MAGIC with `magic_position='eve'`). Executes tasks, collaborates.
- **Position** — `adam` / `eve`. Structural fact about the org.
- **Contact** — A person known to the society. Role: `admin` (WebUI operator), `assigned` (the served user), or `guest` (everyone else).

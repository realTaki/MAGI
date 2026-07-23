# MAGI — Roadmap (C0 → C8)

The project ships in numbered checkpoints (**C0** … **C8**),
each a self-contained deployable slice. Smaller increments
inside a checkpoint (e.g. D.0, D.6, D.17, D.18) are drops
and tracked in the changelog / commit history, not in this
file.

## Status snapshot (2026-07-19)

| Stage | Done / Partial / Next | Headline |
|---|---|---|
| C0 — first-touch deploy | **Done** | WebUI + TG + SQLite + ORM, end-to-end |
| C1.1 — schema baseline | **Done** | ORM + FTS5 + default-root seed |
| C1.2 — employee lifecycle | **Done** | Full CRUD + per-employee LLM routing |
| C1.3 — Alembic + WebUI completion | **Next** | Alembic baseline + `/api/eves` `/api/audit` `/api/login` still pending |
| C2 — chat history | **~90%** | All CRUD/auto-compact/auto-title done; **TG self-serve `/start <code>` still pending**; D.22/D.23/D.24/interrupt/reactions landed |
| C3 — cross-channel dispatcher + audit ingest | **~30%** | Per-employee LLM routing done; real asyncio.gather dispatcher and `/ingest/audit` `/ingest/heartbeat` still placeholder |
| C4 — per-employee persona + memory UI | **~55%** | `action_items.source="eve"` done; **memory + contact + skills blocks now wired into system prompt** (per-chat contact renders real display_name); per-employee SOUL.md, memory management UI still pending |
| C5 — more channels (Email + Calendar) | **0%** | Not started |
| D.28 + D.29 — channel dispatcher + ``user_im_bindings`` table | **Done** | Per-channel IM ids (currently TG chat id) live in a single ``user_im_bindings(uid, channel, im_id)`` table; domain code (agent tools, runner, webui api auth) talks only to ``magi.channels.dispatcher`` and never reads the per-channel IM id directly. Each channel implements a :class:`ChannelAdapter` Protocol; adding a new channel = writing one adapter + registering it. The legacy ``Employee.telegram_id`` column is kept in sync as a read-cache for the bot's inbound path. See ``docs/D.28-channel-dispatcher.md``. |
| C6 — cross-MAGI + cross-employee | **~5%** | Role enum in place; `/api/eves/{id}/dispatch`, cross-employee query still pending |
| C7 — WebSocket stream console | **0%** | Not started |
| C8 — hardening (encryption, degraded mode, audit outbox) | **0%** | Not started |

**Overall**: late C2 / early C3. The two biggest
**Next** items are:

1. **C3 dispatcher** — replace the C0 first-touch
   handler with a real per-channel `asyncio.gather`,
   wire per-employee LLM routing through the
   dispatcher (currently bypassed via the cookie
   resolution path), and stand up `/ingest/audit` +
   `/ingest/heartbeat` for Adam↔EVE.
2. **C4 memory-to-prompt** — call
   `format_memory_block()` +
   `format_contact_block()` + the session active
   block from `loop.py`, then build the `/api/memory`
   WebUI surface.

The plan is reverse-engineered from code comments and
runtime-config intent (C-stage names are referenced in
docstrings, configuration keys, and module docstrings
throughout `magi/agent/` and `magi/node/`). Where the
code is ambiguous, the **Status** column below marks
the item explicitly as **unconfirmed** so future work
can confirm it before sinking time.

> **Conventions**
>
> - **Done** = shipped in v0 (or in an earlier D.x drop) and
>   present in the tree today.
> - **Partial** = the shape is in the code but the
>   documented end-state isn't fully built (e.g. FTS5
>   index built but the search route isn't wired).
> - **Next** = queued for the immediate next checkpoint;
>   concrete code path documented.
> - **Later** = in scope but no ETA.
> - **Unconfirmed** = inferred from code comments; needs
>   user confirmation before being treated as a real
>   commitment.

---

## C0 — First-touch deploy (✅ shipped)

The smallest slice that runs a single node end-to-end and
onboards one admin. All non-essential features are
stubbed or absent.

| Surface | Status | Notes |
|---|---|---|
| WebUI channel (admin login + dashboard) | **Done** | React 19 + TS + Tailwind + Vite, FastAPI backend |
| Telegram channel (single bot, first-touch reply) | **Done** | One bot account per node |
| SQLite as `MAGI_STATE_BACKEND` | **Done** | Default; the only state backend currently wired |
| `meta` table + `settings` table (raw-SQL hand-rolled) | **Done** | `local_db.py` + `settings.py` — pre-ORM, will be replaced by SQLAlchemy in C1 |
| Departments + employees tables (raw-SQL) | **Done** | C1.1 will layer an ORM on top |
| First-touch handler ("I don't know who you are") | **Done** | node `__init__` C0 path; C3 replaces with the real dispatcher |
| Single-node deploy (`MAGI_STATE_BACKEND=sqlite`, `MAGI_CHANNELS=webui,telegram`) | **Done** | `node/__init__.py` loops channels in non-blocking first |
| `MAGI_NODE_ROLE=adam` / `eve` presets | **Done** | Pure shorthand for the three axis overrides; see `node/__init__.py` docstring |
| Inline pre-Alembic `ALTER TABLE` migrations | **Done** | `magi/agent/db/migrations.py` — replaced by the first Alembic baseline at end of C1.3 |
| `get_skill_loader` + 3 bundled SKILL.md examples | **Done** | `magi/skills/{codebase_search,reminder_template,web_lookup}/SKILL.md` |
| LLM providers (Anthropic + Minimax via Anthropic-API-compat) | **Done** | `magi/agent/llm/{anthropic,claude,minimax}.py` |
| Memory subsystem (magi / contacts / session) | **Partial** | Tables + tools exist; agent loop doesn't render them yet |
| Bash tool (run / output / kill) | **Done** | `magi/agent/tools/bash.py` |
| File tools (read / write / list) | **Done** | `magi/agent/tools/{read_file,write_file,list_files}.py` |
| `edit_file` tool (precise string replacement) | **Done** | `magi/agent/tools/edit_file.py` — `old_str` / `new_str`, requires unique match |
| `read_file` windowed mode (offset / limit) | **Done** | Same file; line-numbered `N|content` output for paged reads |

**Not in C0 (deferred):**

- Postgres state backend — env value exists in `NodeConfig`, init module
  just logs "deferring to C1+".
- Real agent-loop dispatcher — `node/__init__.py` mentions
  "C3 will replace this with the real agent-loop
  dispatcher".
- /start binding flow — currently admin-driven only
  (`onboarding.py`); C2 adds the self-serve
  `/start <code>` path.
- EVE → Adam ingest RPC — the `NodeConfig` knows about
  `MAGI_ADAM_URL` / `MAGI_SHARED_SECRET` but the
  `/ingest/audit` and `/ingest/heartbeat` routes
  don't exist yet.

---

## C1.x — Schema + WebUI surface

The data + dashboard slice. Brings the org into a
shape the operator can manage from the browser, and
gets the data layer to Alembic (the migration
discipline C0 deliberately punted on).

### C1.1 — Schema baseline (✅ shipped)

| Item | Status | Notes |
|---|---|---|
| SQLAlchemy `Base` + per-table ORM models (employees / departments / action_items / token_usage / chat_sessions / chat_messages) | **Done** | `magi/agent/db/models_*.py` |
| `init_orm` replaces the raw-SQL hand-rolled writes | **Done** | engine `init_orm` eager-imports every model |
| Inline `ALTER TABLE` pass for columns the SQLAlchemy `create_all` can't add | **Done** | `magi/agent/db/migrations.py` |
| FTS5 virtual table + sync triggers on `chat_messages.text` | **Done** | Same file; trigram tokenizer for CJK-friendly substring search |
| Default-root seed ("MAGI.org") | **Done** | `engine._seed_default_root` |
| Departments tree (parent_id self-FK + manager_id) | **Done** | Cycles prevented at API layer (out-of-scope for C1.1 per `departments.py` comment) |
| `api_key` plain-text in `employees` (C0 → C8 hardening plan to encrypt) | **Done** | C8 encrypts at rest with `MAGI_SECRET` |

### C1.2 — Employee lifecycle

| Item | Status | Notes |
|---|---|---|
| `api/employees` router: full CRUD + assign to dept | **Done** | `magi/channels/webui/api/employees.py` |
| Employee lifecycle fields (email, status, quiet hours) | **Later** | Referenced in `models_employee.py` docstring |
| `api/departments` manager picker v2 | **Later** | Current C1.1 picker is minimal; full picker scheduled in C1.2 + C1.3 |
| Per-employee LLM provider routing (assigned → own key) | **Done** | `Employee.provider` + `Employee.api_key` are read by `loop.py` on each `handle_message`; admin row currently doubles as the per-employee key source until C3 wires the dispatcher properly |

### C1.3 — Alembic baseline + WebUI completion

| Item | Status | Notes |
|---|---|---|
| First Alembic baseline migration (replaces `migrations.py` `_run_inline_migrations`) | **Next** | Multiple comments call this out: "end of C1.3" |
| All remaining C1.1 routes: `/api/eves`, `/api/skills`, `/api/audit`, `/api/login` | **Partial** | `/api/skills` is wired (`KnowledgeTab` Skills list); `/api/eves`, `/api/audit`, `/api/login` not yet |
| Encrypted-at-rest `api_key` (C0 caveat → done) | **Later** | `MAGI_SECRET` plumbed through |

---

## C2 — TG self-serve binding + chat history

The slice where every employee can finish onboarding
without an admin, and chat history is browsable
end-to-end.

| Item | Status | Notes |
|---|---|---|
| `/start <code>` self-serve binding flow | **Next** | `app.py: "C2 will replace with a /start <code> flow"` |
| Per-employee telegram_id binding on the `employees` row | **Done** | C1.1 added the column; binding is admin-only until C2 lands |
| `api/chat/sessions` CRUD (D.6) | **Done** | `magi/channels/webui/api/chat_sessions.py` — full session lifecycle (list, get, create, delete, search, message pagination) |
| `chat_messages` table + FTS5 search (D.18) | **Done** | `memory/session/tables.py` + `migrations.py` FTS5 sync |
| Auto-compact (D.17) — `archive` table + tail count | **Done** | `_maybe_compact` in `loop.py`; `archive` field on `Session`; `active_tail_count` snapshot |
| Auto-title worker (D.7) | **Done** | `memory/session/auto_title.py` |
| Session identity keyed by `Employee.id`, not tgid (D.23) | **Done** | `SessionStore` first arg is `uid`; row carries `tgid` as the per-channel delivery address; cross-channel read scope is "everything owned by this uid" |
| Cross-channel session write guard (D.22) | **Done** | `SessionStore.append_messages` raises `ChannelMismatchError` when stored `channel != caller channel`; mapped to HTTP 403 `chat.session_channel_mismatch` in `chat.py` |
| Cookie identity by `Employee.id`, not telegram_id (D.24) | **Done** | `magi_session` cookie value = the uid (Employee PK); gate helpers (`_admin_uid` / `AdminGate`) look up by primary key; `/me` returns `{uid, telegram_id, display_name}` — Helpers: `_admin_uid` / `_uid_for_tgid`. (Pre-D.27 the same helpers carried the older `_*` (Employee-row identifier) `-suffixed` names; the rename is cosmetic — the resolution shape is identical.) |
| TG side: one persistent session per chat, auto-created | **Done** | `_resolve_or_create_tg_session` (D.10) |
| TG inbound → session store before `handle_message` | **Done** | D.10/D.11 — channel-mismatch guard + audit trail before LLM call |
| Interrupt-aware agent loop (D.21) | **Done** | `_drain_pending_user_messages` splices follow-up user messages into the live tool loop and resets `iterations_run` |
| TG `concurrent_updates=True` (so interrupt poll has new messages to drain) | **Done** | Without this, python-telegram-bot's dispatcher serialises per-chat updates and the interrupt poll never fires |
| `send_message` tool out-of-band channel | **Done** | TG `_handle_employee_message` injects a `tg_send_callback` into `handle_message`; tool calls `bot.send_message(...)` on the python-telegram-bot client (the client's wire kwarg name is fixed by the TG vendor API); the value comes from `chat_sessions.tgid` |
| TG inbound reactions: read-emoji + done-emoji | **Done** | Configurable via `/api/tg-settings/read-reaction` + `/done-reaction` (5 emoji each, validated against Telegram's `ReactionEmoji` whitelist); default 👀 / 🏆 |

**Not in C2 (deferred):**

- Per-employee SOUL.md — `loop.py: "C4 will move this
  to a per-employee"`. Currently `SOUL.md` is
  workspace-global.
- Cross-employee chat routing (C6+) — see C6.
- Self-serve `/start <code>` — still admin-driven.

---

## C3 — Cross-channel dispatcher + audit ingest

The slice where EVE and Adam are distinct node roles
that talk to each other.

| Item | Status | Notes |
|---|---|---|
| Real agent-loop dispatcher (replace C0 first-touch handler) | **Next** | `node/__init__.py: "C3 will replace this with the real agent-loop dispatcher"` |
| Multi-channel asyncio.gather for the runtime | **Partial** | TG already runs in a daemon thread with `concurrent_updates=True`; channels share the same process but aren't yet gathered as concurrent tasks in `node/__init__.py` |
| `/ingest/audit` route (EVE → Adam) | **Next** | `app.py: "C3 — /ingest/audit, /ingest/heartbeat"` |
| `/ingest/heartbeat` route (EVE → Adam) | **Next** | Same |
| Adam ↔ EVE auth via `MAGI_SHARED_SECRET` | **Done** | `NodeConfig` knows the env vars; HTTP client + server impl lands in C3 |
| Per-employee LLM provider routing (assigned → own key) | **Done** | `Employee.provider` + `Employee.api_key` are read by `loop.py` on each `handle_message`; admin row currently doubles as the per-employee key source |
| Per-channel channel + dept policy (dept must be non-NULL) | **Later** | `engine.py: "C3 / C6 will likely require every employee to belong to a non-root department"` |

---

## C4 — Per-employee persona + proactive EVE follow-ups

The slice where EVE starts to feel less like a tool and
more like a colleague. SOUL moves from a global
file to per-employee, and the operator can see EVE-
driven action items.

| Item | Status | Notes |
|---|---|---|
| Per-employee SOUL.md (replacing workspace-global) | **Next** | `loop.py: "C4 will move this to a per-employee"`, `soul.py: "Per-employee personas are C4+"` |
| `action_items.source = "eve"` for proactive follow-ups | **Done** | `models_action_item.py` already documents this; C4 is when the EVE side writes them |
| `action_items.priority = "high"` for time-sensitive follow-ups | **Done** | Same |
| `action_items.payload_json` per-kind structured fields | **Later** | YAGNI for the rows we can foresee (per the model docstring); add when C4 needs structured per-kind fields |
| Memory subsystem fully wired into `loop.py` prompt assembly | **Done** | `_build_system_prompt` in `loop.py` renders SOUL → memory (important + ongoing in-flight) → contact (per-chat, real display_name) → skills; tests in `test_agent_system_prompt.py` pin ordering + scope + resilience |
| Memory management UI in WebUI (operator sees / edits / deletes rows) | **Next** | Currently the table is LLM-only; no `/api/memory` route; `KnowledgeTab` shows skills but not memory/contacts |
| Per-employee settings (C4+ setting keys) | **Later** | `system_settings.py: "A future C4+ setting"` |

---

## C5 — More channels (Email + Calendar)

The slice where EVE is no longer a Telegram-only bot.

| Item | Status | Notes |
|---|---|---|
| Email channel (IMAP/SMTP ingest + send) | **Later** | `onboarding.py: "C5 will onboard Email or Calendar"` |
| Calendar channel (Google / Microsoft) | **Later** | Same |
| Cross-channel message dedup (an inbound from email + a forwarded TG copy of the same thread) | **Unconfirmed** | Inferred from "channel-agnostic identity" in the product spec |

---

## C6 — Cross-MAGI + cross-employee semantics

The slice where multiple EVE nodes can talk (through
Adam) and the company has more than one employee
that needs to be visible across them.

| Item | Status | Notes |
|---|---|---|
| `employee.role` = `"employee"` / `"guest"` semantics (not just `"admin"` / `"assigned"`) | **Done** | `models_employee.py` already supports all four; C1.1 writes `admin` / `assigned`, C6 fills the rest |
| Eve-of-another-MAGI bot refusal ("you can talk to your own EVE, not mine") | **Later** | `models_employee.py: "C6+ (cross-MAGI access, public visitors)"` |
| `api/eves/{id}/dispatch`, `api/eves/{id}/recall` | **Next** | `app.py: "C6 — /api/eves/{id}/dispatch, /api/eves/{id}/recall"` |
| Cross-employee query / summary (operator-side, in Adam) | **Later** | Per the product spec: "汇总 / 跨员工查询 in Adam, not EVE → EVE" |
| Per-employee LLM key per assigned employee enforced everywhere | **Next** | C3 wires the dispatcher; C6 closes the loop on cross-employee queries |

---

## C7 — WebSocket stream console

The slice where the operator watches EVE think in
real time.

| Item | Status | Notes |
|---|---|---|
| `GET /ws/console` WebSocket stream | **Next** | `app.py: "C7 — WebSocket console stream (/ws/console)"` |
| `/chat/send` becomes non-blocking (replaces C0 sync reply) | **Next** | `app.py: "v0 non-streaming; C7 swaps"` |
| Tool-by-tool stream (LLM token stream + tool call + tool result) | **Unconfirmed** | Inferred from "WebSocket console" — exact payload shape TBD |

---

## C8 — Hardening (encryption, degraded mode, audit outbox)

The slice where MAGI is ready for an enterprise's
worst-day operational scenarios.

| Item | Status | Notes |
|---|---|---|
| Encrypted-at-rest `employees.api_key` via `MAGI_SECRET` | **Next** | `models_employee.py: "C8 hardening pass encrypts at rest with a deployer-supplied MAGI_SECRET"` |
| Symlink / path-traversal containment for file tools (replace current `Path.resolve()` trust model) | **Next** | `_safe_path.py: "C8 hardening can swap in realpath() plus a containment check"` |
| Audit outbox lag monitoring + degraded-mode alert | **Next** | `app.py: "audit outbox lag) is added in C8 alongside the hardened degraded-mode"` |
| Operator up-time SLO dashboard | **Unconfirmed** | Inferred from the same C8 comment block |
| Multi-region failover (Adam HA) | **Unconfirmed** | Inferred from "degraded-mode" — concrete shape TBD |

---

## Cross-cutting (any stage)

| Item | Status | Notes |
|---|---|---|
| First Alembic baseline (replaces `_run_inline_migrations`) | **Next** (end of C1.3) | Multiple callouts in code |
| Bash tool — structured result model / OpenAI schema | **Later** | See [bash-tool-evolution.md](memory/bash-tool-evolution.md) for the trigger conditions |
| `tools/bash.py` one-file three-tool split | **Later** | Current threshold is 200 lines per class |
| `tokens.py` to `llm/` | **Done** | (in this refactor series) |
| File tools — `edit_file` (precise string replacement) | **Done** | `magi/agent/tools/edit_file.py` — `old_str` / `new_str`, requires unique match |
| File tools — `read_file` windowed mode (offset / limit) | **Done** | Same file; line-numbered `N|content` output for paged reads |
| File tools — `tiktoken` token-aware truncation | **Later** | Trigger: LLM complains "truncated but still too much" — adds a native dep |
| File tools — `edit_file` `replace_globally` switch | **Later** | Trigger: real need for "rename var across whole file" workflows |
| MCP — per-server rate limit / auto-pause on flake | **Later** | Trigger: dashboard reports "MCP server flake" — pause for N min after M timeouts |
| MCP — tool call audit log (name / args / duration / result size) | **Later** | Trigger: operator wants to know "how many times was `fetch` called last week" |
| MCP — `mcp.json` hot-reload | **Later** | Trigger: deployer wants to add a server without restarting MAGI |
| MCP — tool output token cap (10 MB fetch explodes context) | **Later** | Trigger: any MCP tool call surfaces a "context length exceeded" downstream |
| Skills — `load_skill` body section slicing (offset / limit) | **Later** | Trigger: skill body > 10 KB and LLM wants a specific section |
| Skills — usage audit (which skills the LLM calls, how often) | **Later** | Trigger: operator wants to optimise the skill catalog (drop unused, expand popular) |
| Skills — `allowed-tools` enforcement (frontmatter field is read but not yet enforced) | **Later** | Trigger: operator wants "this employee can only use read_file, not bash" |
| Skills — `license` / `allowed_tools` / `metadata` optional frontmatter | **Done** | `magi/agent/memory/session/auto_title.py`-adjacent; skill loader reads these for display, not enforcement yet |
| Settings UI consolidation (Agent loop + Auto-compact → one card) | **Done** | `SettingsAgentCard` replaces the two old cards; navPersona renamed to "个性化设置" |
| WebUI LoginPage "用 Telegram ID 登录" subtitle | **Removed** | Future IM platforms won't all be TG |

---

## Recent drops (post-ROADMAP, documented here for completeness)

Work that landed after this file was last refreshed.
Grouped by D.x number for cross-reference with the
commit history.

### D.10 / D.11 — TG session persistence + D.22 cross-channel guard

- TG inbound messages persist to `chat_sessions` /
  `chat_messages` (SQLite) BEFORE `handle_message`
  runs, the same way WebUI does. One persistent
  session per TG chat (`_resolve_or_create_tg_session`
  reuses the most recent TG-owned session, mints a
  new one otherwise).
- **D.22 cross-channel write guard**:
  `SessionStore.append_messages` raises
  `ChannelMismatchError` when the stored row's
  `channel != caller channel`. Read paths
  (`get` / `list_summaries`) intentionally don't
  gate by channel — same employee can browse TG
  history from WebUI. The WebUI chat API maps the
  exception to HTTP 403 `chat.session_channel_mismatch`.

### D.17 — Auto-compact

- Long sessions accumulate context; once the
  in-memory message list crosses
  `context_window × threshold_pct%`, the agent
  loop calls the LLM to summarise older messages
  into a single system message, archives the
  originals, and keeps only the most recent N in
  the active list. All three knobs are configurable
  from the WebUI Settings → Agent 设置 panel.
- FTS5 search still hits the active tail; archived
  rows are forensic-only and require an opt-in
  `include_archived=true` flag on the messages
  endpoint.

### D.18 — FTS5 search + sessions SQLite migration

- `chat_messages` got an FTS5 virtual table with
  the trigram tokenizer (CJK-friendly substring
  matches).
- The session store migrated from JSON files
  under `<workspace>/memories/sessions/<tgid>/`
  to SQLite rows. Migration ran
  `migrate_from_json` once at boot.

### D.21 — Interrupt-aware agent loop

- `_drain_pending_user_messages` polls the session
  store at the top of every loop iteration; when a
  new user message lands (because the channel
  handler persisted it before calling
  `handle_message`), it's spliced in at a safe
  boundary in the tool_use / tool_result chain and
  `iterations_run` is reset so the LLM gets a
  fresh budget to react.
- **TG side**: requires
  `Application.builder().concurrent_updates(True)`
  in `start_bot` — without it, python-telegram-bot
  serialises per-chat updates at the dispatcher
  level and the interrupt poll never has anything
  new to drain (the second user message sits in the
  bot's queue until the prior handler fully
  returns). Test in `test_tg_concurrent_updates.py`.

### D.23 — Session identity keyed by `Employee.id`

- `SessionStore` first arg is `uid: int`,
  not `tgid: str`. `tgid` is now keyword-only
  on `create()` and stamps the per-channel delivery
  address on the row's `tgid` column. This lets the
  same Employee own sessions across channels (TG +
  WebUI) with a single identity.
- Read scope: anything whose `uid` matches
  the caller (cross-channel by design — see Open
  Question 7).
- Write scope: cross-channel writes raise
  `ChannelMismatchError` (D.22).

### D.24 — Cookie identity by `Employee.id`

- `magi_session` cookie value is the uid (Employee PK,
  was `str(telegram_id)`). `AdminGate` reads by primary
  key. The login flow's `_resolve_uid_for_tgid()` helper
  translates a TG tgid → uid before `verify_login_code`
  sets the cookie. (Pre-D.27 this helper was named
  `_uid_for_tgid`; the rename from the older helper is cosmetic —
  the resolution shape is identical.)
- `/api/auth/me` returns `{uid, telegram_id,
  display_name, is_super_admin}` — the operator's
  cross-channel identity. D.26 also clarified: there is
  no separate "chatter" identity; the cookie's uid IS
  the person MAGI is talking to, never a chat id.

### TG reactions (read-emoji + done-emoji)

- TG inbound gets a configurable read-emoji
  (default 👀) as soon as the handler starts;
  replaced by a configurable done-emoji (default 🏆)
  when the LLM reply lands. TG itself auto-clears
  the prior reaction when a new one is set on the
  same message — no need for a "clear then set"
  two-step.
- Whitelist of 5 emoji each, validated against
  Telegram's `ReactionEmoji` enum at write time.
  Configurable from `/api/tg-settings/read-reaction`
  and `/done-reaction`.

### Settings UI consolidation

- "Agent 循环" + "自动压缩" merged into one card
  "Agent 设置" (`SettingsAgentCard`). The two
  sub-sections have independent state (their own
  save buttons); no combined PUT.
- "Persona" sidebar entry renamed to "个性化设置"
  (the underlying `id` is unchanged for
  back-compat).
- LoginPage "用 Telegram ID 登录" subtitle removed
  — future IM platforms won't all be TG.

### `send_message` tool out-of-band channel

- New tool: LLM can deliver an intermediate
  message without ending the tool loop (e.g.
  "Reading your SOUL..."). WebUI rejects with
  `is_error=true` (operator already sees the
  final reply inline). TG side requires the
  channel handler to inject `tg_send_callback`
  into `handle_message`'s kwargs; without it, the
  tool returns "TG callback not wired into the
  tool context". Test in
  `test_tg_send_message_callback.py`.

### System-prompt assembly wires all four blocks

- `_build_system_prompt` in `loop.py` now
  composes, in fixed order: **SOUL** →
  **Long-term memory** (MAGI's important +
  ongoing in-flight rows, scoped to
  `owner_id == uid`) → **Current
  chatter** (the User's self-contact record,
  looked up as `(owner_id=uid, person_id=uid)` —
  rendered with the Employee's real
  `display_name ?? name`, **not** the raw
  `person_id` FK) → **Available skills**
  (frontmatter summary).
- Each block short-circuits on empty rows so a
  fresh deploy still gets a sensible prompt.
  ORM failures inside any block degrade
  gracefully (the block is dropped, the rest
  of the prompt still renders).
- Tests in `test_agent_system_prompt.py` pin:
  block ordering, per-uid scope, the self-
  contact block (uid == chatter, no second
  "person on the other end" lookup), the
  `display_name` rendering invariant, and the
  four resilience cases (memory / contact
  ORM failure, empty blocks, etc.).
- D.26 collapsed the per-chatter lookup:
  pre-D.26 the resolver ran on `tgid`
  (Telegram digits) and consulted a
  different person's contact row. With
  `chat_sessions.tgid` removed from the agent
  loop and the cookie carrying the uid
  directly, there is only ever one User per
  chat — the contact block is the User's own
  self-record.
  up via a tool call.

### Prompt text centralized in `magi/agent/prompts/`

- All natural-language text the runtime
  emits to the LLM lives in one place:
  `soul.md`, `fallback_persona.md`,
  `chat_titles.md`, `compaction.md`,
  `bot_replies.yaml`, plus the three new
  per-block templates:
  - `memory_block.md` — header + intro +
    per-kind sub-section headings
    (`### 重要的事`, `### 正在进行`)
  - `contact_block.md` — `## Current chatter`
    header + intro
  - `skills_block.md` — `## Available skills`
    header + intro
- Loader at `magi/agent/prompts/__init__.py`
  caches each file once per process; the
  cache survives across requests. A future
  C8 file-watcher will close the loop so an
  operator edit takes effect without a
  restart.
- The Python formatters (`format_memory_block`,
  `format_contact_block`, `format_skills_block`)
  no longer carry prose. They load the
  template, parse the `### ` markers (memory
  block only), and append runtime data. An
  operator tuning prompt wording now opens
  the `.md` file in an editor, never the
  Python file.

### Timestamp helpers unified (deprecation-warning cleanup)

- Python 3.12 emits `DeprecationWarning`
  for `datetime.utcnow()`. Two helpers now
  replace it:
  - `magi.agent.db.base.utcnow_naive()` —
    used by every ORM `default=` /
    `onupdate=`. Lives in `db/base.py`
    (lowest layer) so model files import
    it without triggering the
    `memory → contacts → tools → db`
    circular import.
  - `magi.agent.memory.session.ids.utcnow_iso()`
    — session-package ISO strings (the
    `String(32)` columns rather than
    `DateTime`).
- Production code now contains zero
  `datetime.utcnow()` calls; the deprecation
  warnings in the test run are all from
  test files (intentionally left alone —
  tests are short-lived and don't need the
  migration).
- DB column type still `DateTime` (naive,
  UTC). Switching to `DateTime(timezone=True)`
  is a future Alembic migration task that
  moves the schema column type, the store-level
  ISO serialisation, and the cross-module
  ordering all together — see
  [ROADMAP C1.3 Alembic baseline](file:///Users/.../ROADMAP.md#c13--alembic-baseline--webui-completion).

## Open questions for the user

These show up while reading the code but the code
itself is silent on which direction to go. Worth
asking before sinking more time:

1. **C1.3 Alembic migration** — replace
   `_run_inline_migrations` with a real Alembic
   baseline, or keep the inline pass for one more
   stage and migrate at end of C2?
2. **C2 self-serve `/start <code>`** — code-generated
   one-time codes (operator prints), or QR-coded
   deep link from the WebUI? Comment says "code
   flow that uses the right thing" without
   specifying.
3. **C4 per-employee SOUL.md** — stored as a row in
   the DB (new `employee_soul` table) or as a file
   under `<workspace>/employees/<id>/SOUL.md`?
4. **C7 WebSocket payload** — what fields go in each
   frame? (token deltas? tool calls? raw blocks?)
5. **C8 `MAGI_SECRET` distribution** — how does the
   deployer get the secret into the container?
   File-mounted? Env var? Vault? The encryption
   code needs an answer before the rollout.
6. **D.24 cookie compatibility** — old cookies stored
   `str(telegram_id)`; the new `str(employee.id)`
   breaks existing sessions on upgrade. For dev this
   is fine, but pre-production deploys need a
   migration path (force re-login, or transparently
   re-resolve `tgid → employee.id` on first request
   that 401s on a `tgid`-shaped cookie). What's the
   preferred approach?
7. **D.23 cross-channel read vs write semantics** —
   read paths (`get`, `list_summaries`,
   `get_messages_page`) intentionally do **not**
   gate by channel — the operator can browse their
   TG history from the WebUI. This is currently
   implicit in the store. C6 may want a UI toggle
   ("WebUI sessions only / all sessions") to avoid
   the surprise of seeing TG-only threads in the
   WebUI sidebar. Worth deciding before the UI
   grows around the implicit behaviour.
8. **Skill hot-reload** — operator edits
   `workspace/skills/<name>/SKILL.md`, currently
   requires a MAGI restart to take effect. The
   skill loader supports re-scan-on-boot; inotify /
   polling is one-off cheap. Trigger: operator
   complains "I edited the skill and it didn't pick
   up".

---

## Related docs

- [README.md](../README.md) — the one-paragraph product
  positioning
- [magi-product-spec.md](memory/magi-product-spec.md) —
  the "why we built it this way" memory note
- [overall-refactor-plan.md](memory/overall-refactor-plan.md)
  — what the per-package code looks like today
- [bash-tool-evolution.md](memory/bash-tool-evolution.md)
  — deferred bash tool follow-ups
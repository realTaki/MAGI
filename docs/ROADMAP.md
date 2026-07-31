# MAGI — Roadmap (C0 → C8)

> **Documentation note (2026-07):** this file preserves detailed historical
> planning notes and completed milestones. For the current terminology, use
> **MAGIS = MAGI Society** and **MAGIC = MAGI Citizen**. Historical sections
> below may retain the old terms or old schema sketches; they are not the
> current API or database contract. See [Architecture](ARCHITECTURE.md) and
> [database migrations](database-migrations.md) for the current implementation.

> **Posture (2026-07-23 refresh):** MAGI is reframed as **Modular Agentic
> Group Intelligence** — a system composed of autonomous agents ("MAGIs")
> coordinating as a group. Today we ship two **archetypes**: a manager
> archetype (`adam`, *Adaptive Distributed Agent Matrix*) and a worker
> archetype (`eve`, *Enhanced Virtual Expert*). The runtime is the same;
> the archetype is configuration. The body of this file still talks about
> checkpoints C0–C8 and the work that lives behind them; the
> "Post-refactor follow-ups" section at the end is the backlog for
> propagating the reframe into code (table rename, route aliases,
> i18n cleanups, etc.). That work is doc-tracked here, **not yet
> applied to the code** — it lands in later PRs.

The project ships in numbered checkpoints (**C0** … **C8**),
each a self-contained deployable slice. Smaller increments
inside a checkpoint (e.g. D.0, D.6, D.17, D.18) are drops
and tracked in the changelog / commit history, not in this
file.

## Status snapshot (2026-07-23)

| Stage | Done / Partial / Next | Headline |
|---|---|---|
| C0 — first-touch deploy | **Done** | WebUI + TG + SQLite + ORM, end-to-end |
| C1.1 — schema baseline | **Done** | ORM + FTS5 + default-root seed |
| C1.2 — User lifecycle | **Done** | Full CRUD + per-User LLM routing |
| C1.3 — Alembic + WebUI completion | **Partial** | Versioned Alembic baseline is live; `/api/eves` `/api/audit` `/api/login` still pending |
| C2 — chat history | **~90%** | All CRUD/auto-compact/auto-title done; **TG self-serve `/start <code>` still pending**; D.22/D.23/D.24/interrupt/reactions landed |
| C3 — cross-channel dispatcher + audit ingest | **~30%** | Per-User LLM routing done; real asyncio.gather dispatcher and `/ingest/audit` `/ingest/heartbeat` still placeholder |
| C4 — per-MAGI persona + memory UI | **~55%** | `action_items.source="eve"` done; **memory + contact + skills blocks now wired into system prompt** (per-chat contact renders real display_name); per-MAGI SOUL.md, memory management UI still pending |
| C5 — more channels (Email + Calendar) | **0%** | Not started |
| D.28 — channel dispatcher | **~80%** (core done, stragglers remain) | Architected in ``magi/channels/dispatcher.py``: domain code (tools, runner, loop) talks only to the dispatcher in uid+channel+session_id. ``chat_sessions.tgid`` renamed to ``delivery_address``. TG adapter in ``channels/telegram/adapter.py`` implements the ``ChannelAdapter`` Protocol (send/lookup_im_id/bind_im_id). ``send_message`` tool uses ``dispatcher.send_to_session``; runner no longer has ``_tg_send_callback``. Remaining: ``onboarding.py`` Pydantic schemas still expose ``tgid`` field names; ``contacts/store.py``, ``session/ids.py``, ``session/migration.py`` have tgid-named helpers. D.29 (``magi_im_bindings`` table) is the follow-up. |
| C6 — cross-MAGI + cross-User | **~5%** | Role enum in place; `/api/eves/{id}/dispatch`, cross-User query still pending |
| C7 — WebSocket stream console | **0%** | Not started |
| C8 — hardening (encryption, degraded mode, audit outbox) | **0%** | Not started |

**Overall**: late C2 / early C3. The two biggest
**Next** items are:

1. **C3 dispatcher** — replace the C0 first-touch
   handler with a real per-channel `asyncio.gather`,
   wire per-User LLM routing through the
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
onboards one operator. All non-essential features are
stubbed or absent.

| Surface | Status | Notes |
|---|---|---|
| WebUI channel (operator login + dashboard) | **Done** | React 19 + TS + Tailwind + Vite, FastAPI backend |
| Telegram channel (single bot, first-touch reply) | **Done** | One bot account per node |
| SQLite as `MAGI_STATE_BACKEND` | **Done** | Default; the only state backend currently wired |
| `meta` table + `settings` table | **Done** | `meta` remains a raw bootstrap KV; `settings` is now an ORM model behind the compatibility facade |
| Departments + Users tables (raw-SQL) | **Done** | C1.1 will layer an ORM on top |
| First-touch handler ("I don't know who you are") | **Done** | node `__init__` C0 path; C3 replaces with the real dispatcher |
| Single-node deploy (`MAGI_STATE_BACKEND=sqlite`; channels from `settings.channels.enabled`) | **Done** | `node/__init__.py` reads enabled channels from the DB, not `MAGI_CHANNELS` |
| `MAGI_NODE_ROLE=adam` / `eve` archetype presets | **Done** | Pure shorthand for the three axis overrides; see `node/__init__.py` docstring |
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
- /start binding flow — currently operator-driven only
  (`onboarding.py`); C2 adds the self-serve
  `/start <code>` path.
- EVE → Adam ingest RPC — the `NodeConfig` knows about
  `MAGI_ADAM_URL` / `MAGI_SHARED_SECRET` but the
  `/ingest/audit` and `/ingest/heartbeat` routes
  don't exist yet.

---

## C1.x — Schema + WebUI surface

The data + dashboard slice. Brings the workspace into a
shape the operator can manage from the browser, and
gets the data layer to Alembic (the migration
discipline C0 deliberately punted on).

### C1.1 — Schema baseline (✅ shipped)

| Item | Status | Notes |
|---|---|---|
| SQLAlchemy `Base` + per-table ORM models (Contact / MAGIS / MAGIC / action_items / token_usage / chat_sessions / chat_messages) | **Done** | `magi/agent/db/models_*.py` — people live in the `contacts` table (the `employees` rename was completed via the inline ``ALTER TABLE employees RENAME TO contacts`` migration); `MAGIS` (group tree) + `MAGIC` (individual agent) carry the org tree |
| `init_orm` replaces the raw-SQL hand-rolled writes | **Done** | engine `init_orm` eager-imports every model |
| Alembic versioned schema migrations | **Done** | `alembic.ini` + `magi/agent/db/alembic/versions`; `init_orm` runs `upgrade head` |
| Legacy inline `ALTER TABLE` adoption pass | **Done** | `magi/agent/db/migrations.py`, runs only for databases without `alembic_version` |
| FTS5 virtual table + sync triggers on `chat_messages.text` | **Done** | folded into Alembic `0001_baseline` (no separate `0002_fts5`); trigram tokenizer for CJK-friendly substring search |
| Default-root seed ("MAGI") | **Done** | `engine._seed_default_root` |
| Departments tree (parent_id self-FK + manager_id) | **Done** | Cycles prevented at API layer (out-of-scope for C1.1 per `departments.py` comment) |
| `api_key` plain-text in User table (C0 → C8 hardening plan to encrypt) | **Done** | C8 encrypts at rest with `MAGI_SECRET` |

### C1.2 — User lifecycle

| Item | Status | Notes |
|---|---|---|
| `api/contacts` router: full CRUD + assign role | **Done** | `magi/channels/webui/api/contacts.py` (formerly `employees.py`; the dept picker went away with the `departments` table) |
| Contact lifecycle fields (email, status, quiet hours) | **Later** | Referenced in `models_contact.py` docstring |
| `/api/magis` + `/api/magic` for MAGIS + MAGIC rows | **Done** | `magi/channels/webui/api/magis.py` manages the MAGIS tree; `magic.py` manages MAGIC Citizens; replaces the old `api/departments`. |
| Per-User LLM provider routing (assigned → own key) | **Done** | `User.provider` + `User.api_key` are read by `loop.py` on each `handle_message`; operator row currently doubles as the per-User key source until C3 wires the dispatcher properly |

### C1.3 — Alembic baseline + WebUI completion

| Item | Status | Notes |
|---|---|---|
| First Alembic baseline migration (replaces `migrations.py` `_run_inline_migrations`) | **Done** | `0001_baseline` adopts existing DBs and creates fresh schemas; the current chain ends at `0007_swap_magic_magis_tables` — see [docs/database-migrations.md](database-migrations.md). |
| All remaining C1.1 routes: `/api/eves`, `/api/skills`, `/api/audit`, `/api/login` | **Partial** | `/api/skills` is wired (`KnowledgeTab` Skills list); `/api/eves`, `/api/audit`, `/api/login` not yet |
| Encrypted-at-rest `api_key` (C0 caveat → done) | **Later** | `MAGI_SECRET` plumbed through |

---

## C2 — TG self-serve binding + chat history

The slice where every assigned User can finish onboarding
without an operator, and chat history is browsable
end-to-end.

| Item | Status | Notes |
|---|---|---|
| `/start <code>` self-serve binding flow | **Next** | `app.py: "C2 will replace with a /start <code> flow"` |
| Per-User telegram_id binding on the User row | **Done** | C1.1 added the column; binding is operator-only until C2 lands |
| `api/chat/sessions` CRUD (D.6) | **Done** | `magi/channels/webui/api/chat_sessions.py` — full session lifecycle (list, get, create, delete, search, message pagination) |
| `chat_messages` table + FTS5 search (D.18) | **Done** | `memory/session/tables.py` + `migrations.py` FTS5 sync |
| Auto-compact (D.17) — `archive` table + tail count | **Done** | `_maybe_compact` in `loop.py`; `archive` field on `Session`; `active_tail_count` snapshot |
| Auto-title worker (D.7) | **Done** | `memory/session/auto_title.py` |
| Session identity keyed by `User.id`, not tgid (D.23) | **Done** | `SessionStore` first arg is `uid`; row carries `tgid` as the per-channel delivery address; cross-channel read scope is "everything owned by this uid" |
| Cross-channel session write guard (D.22) | **Done** | `SessionStore.append_messages` raises `ChannelMismatchError` when stored `channel != caller channel`; mapped to HTTP 403 `chat.session_channel_mismatch` in `chat.py` |
| Cookie identity by `User.id`, not telegram_id (D.24) | **Done** | `magi_session` cookie value = the uid (User PK); gate helpers (`_operator_uid` / `OperatorGate`) look up by primary key; `/me` returns `{uid, telegram_id, display_name}` — Helpers: `_operator_uid` / `_uid_for_tgid`. (Pre-D.27 the same helpers carried the older `_*` (User-row identifier) `-suffixed` names; the rename is cosmetic — the resolution shape is identical.) |
| TG side: one persistent session per chat, auto-created | **Done** | `_resolve_or_create_tg_session` (D.10) |
| TG inbound → session store before `handle_message` | **Done** | D.10/D.11 — channel-mismatch guard + audit trail before LLM call |
| Interrupt-aware agent loop (D.21) | **Done** | `_drain_pending_user_messages` splices follow-up User messages into the live tool loop and resets `iterations_run` |
| TG `concurrent_updates=True` (so interrupt poll has new messages to drain) | **Done** | Without this, python-telegram-bot's dispatcher serialises per-chat updates and the interrupt poll never fires |
| `send_message` tool out-of-band channel | **Done** | TG `_handle_assigned_user_message` injects a `tg_send_callback` into `handle_message`; tool calls `bot.send_message(...)` on the python-telegram-bot client (the client's wire kwarg name is fixed by the TG vendor API); the value comes from `chat_sessions.tgid` |
| TG inbound reactions: read-emoji + done-emoji | **Done** | Configurable via `/api/tg-settings/read-reaction` + `/done-reaction` (5 emoji each, validated against Telegram's `ReactionEmoji` whitelist); default 👀 / 🏆 |

**Not in C2 (deferred):**

- Per-MAGI SOUL.md — `loop.py: "C4 will move this
  to a per-MAGI"`. Currently `SOUL.md` is
  workspace-global.
- Cross-User chat routing (C6+) — see C6.
- Self-serve `/start <code>` — still operator-driven.

---

## C3 — Cross-channel dispatcher + audit ingest

The slice where EVE and Adam are distinct node archetypes
that talk to each other.

| Item | Status | Notes |
|---|---|---|
| Real agent-loop dispatcher (replace C0 first-touch handler) | **Next** | `node/__init__.py: "C3 will replace this with the real agent-loop dispatcher"` |
| Multi-channel asyncio.gather for the runtime | **Partial** | TG already runs in a daemon thread with `concurrent_updates=True`; channels share the same process but aren't yet gathered as concurrent tasks in `node/__init__.py` |
| `/ingest/audit` route (EVE → Adam) | **Next** | `app.py: "C3 — /ingest/audit, /ingest/heartbeat"` |
| `/ingest/heartbeat` route (EVE → Adam) | **Next** | Same |
| Adam ↔ EVE auth via `MAGI_SHARED_SECRET` | **Done** | `NodeConfig` knows the env vars; HTTP client + server impl lands in C3 |
| Per-User LLM provider routing (assigned → own key) | **Done** | `User.provider` + `User.api_key` are read by `loop.py` on each `handle_message`; operator row currently doubles as the per-User key source |
| Per-channel channel + dept policy (dept must be non-NULL) | **Later** | `engine.py: "C3 / C6 will likely require every User to belong to a non-root department"` |

---

## C4 — Per-MAGI persona + proactive EVE follow-ups

The slice where EVE starts to feel less like a tool and
more like a colleague. SOUL moves from a global
file to per-MAGI, and the operator can see EVE-
driven action items.

| Item | Status | Notes |
|---|---|---|
| Per-MAGI SOUL.md (replacing workspace-global) | **Next** | `loop.py: "C4 will move this to a per-MAGI"`, `soul.py: "Per-MAGI personas are C4+"` |
| `action_items.source = "eve"` for proactive follow-ups | **Done** | `models_action_item.py` already documents this; C4 is when the EVE side writes them |
| `action_items.priority = "high"` for time-sensitive follow-ups | **Done** | Same |
| `action_items.payload_json` per-kind structured fields | **Later** | YAGNI for the rows we can foresee (per the model docstring); add when C4 needs structured per-kind fields |
| Memory subsystem fully wired into `loop.py` prompt assembly | **Done** | `_build_system_prompt` in `loop.py` renders SOUL → memory (important + ongoing in-flight) → contact (per-chat, real display_name) → skills; tests in `test_agent_system_prompt.py` pin ordering + scope + resilience |
| Memory management UI in WebUI (operator sees / edits / deletes rows) | **Next** | Currently the table is LLM-only; no `/api/memory` route; `KnowledgeTab` shows skills but not memory/contacts |
| Per-User settings (C4+ setting keys) | **Later** | `system_settings.py: "A future C4+ setting"` |

---

## C5 — More channels (Email + Calendar)

The slice where EVE is no longer a Telegram-only bot.

| Item | Status | Notes |
|---|---|---|
| Email channel (IMAP/SMTP ingest + send) | **Later** | `onboarding.py: "C5 will onboard Email or Calendar"` |
| Calendar channel (Google / Microsoft) | **Later** | Same |
| Cross-channel message dedup (an inbound from email + a forwarded TG copy of the same thread) | **Unconfirmed** | Inferred from "channel-agnostic identity" in the product spec |

---

## C6 — Cross-MAGI + cross-User semantics

The slice where multiple EVE nodes can talk (through
Adam) and the workspace has more than one User
that needs to be visible across them.

| Item | Status | Notes |
|---|---|---|
| `Contact.role` = `"user"` / `"guest"` semantics (not just `"admin"` / `"assigned"`) | **Done** | `models_contact.py` (formerly `models_employee.py`) already supports all four; C1.1 writes `admin` / `assigned`, C6 fills the rest |
| Eve-of-another-MAGI bot refusal ("you can talk to your own EVE, not mine") | **Later** | `models_contact.py: "C6+ (cross-MAGI access, public visitors)"` |
| `api/eves/{id}/dispatch`, `api/eves/{id}/recall` | **Next** | `app.py: "C6 — /api/eves/{id}/dispatch, /api/eves/{id}/recall"` |
| Cross-User query / summary (operator-side, in Adam) | **Later** | Per the product spec: "汇总 / 跨 User 查询 in Adam, not EVE → EVE" |
| Per-User LLM key per assigned User enforced everywhere | **Next** | C3 wires the dispatcher; C6 closes the loop on cross-User queries |

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

The slice where MAGI is ready for a workspace's
worst-day operational scenarios.

| Item | Status | Notes |
|---|---|---|
| Encrypted-at-rest `users.api_key` via `MAGI_SECRET` | **Next** | `models_user.py: "C8 hardening pass encrypts at rest with a deployer-supplied MAGI_SECRET"` |
| Symlink / path-traversal containment for file tools (replace current `Path.resolve()` trust model) | **Next** | `_safe_path.py: "C8 hardening can swap in realpath() plus a containment check"` |
| Audit outbox lag monitoring + degraded-mode alert | **Next** | `app.py: "audit outbox lag) is added in C8 alongside the hardened degraded-mode"` |
| Operator up-time SLO dashboard | **Unconfirmed** | Inferred from the same C8 comment block |
| Multi-region failover (Adam HA) | **Unconfirmed** | Inferred from "degraded-mode" — concrete shape TBD |

---

## Cross-cutting (any stage)

| Item | Status | Notes |
|---|---|---|
| First Alembic baseline (replaces `_run_inline_migrations`) | **Done** | chain `0001_baseline` → `0002_admin_role_split` → `0006_contact_notes` (HEAD); legacy runner is adoption-only — see [docs/database-migrations.md](database-migrations.md) |
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
| Skills — `allowed-tools` enforcement (frontmatter field is read but not yet enforced) | **Later** | Trigger: operator wants "this User can only use read_file, not bash" |
| Skills — `license` / `allowed_tools` / `metadata` optional frontmatter | **Done** | `magi/agent/memory/session/auto_title.py`-adjacent; skill loader reads these for display, not enforcement yet |
| Settings UI consolidation (Agent loop + Auto-compact → one card) | **Done** | `SettingsAgentCard` replaces the two old cards; navPersona renamed to "个性化设置" |
| WebUI LoginPage "用 Telegram ID 登录" subtitle | **Removed** | Future IM platforms won't all be TG |

---

## Post-refactor follow-ups (2026-07-23 doc-level reframe → code backlog)

The 2026-07-23 reframe changed the **product framing** of MAGI without
touching the runtime. The docs are updated; the code is not yet. The
follow-ups below migrate the code, in rough priority order. The
principle stays minimal-by-default: only migrate when a concrete
trigger fires.

### F1 — Three-table schema (magics + magis + users)

The schema collapses to **three tables**:

| Table | Holds | Position / role lives on |
|---|---|---|
| `magics` | organizations (the council) | n/a |
| `magis` | agents (MAGI runtime processes) | `magis.position` ∈ {`adam`, `eve`} |
| `users` | people (MAGI's contact directory) | `users.role` ∈ {`admin`, `assigned`, `user`, `guest`} |

`magis.position` and `users.role` are **orthogonal axes**:

- A `MAGIC`'s `position` is intrinsic to the agent's role in the
  MAGIS's org structure (1 ADAM + N EVE per MAGIS). It is **not**
  derived from which User logs in or which User is being served.
- A `User`'s `role` describes the person's service relationship
  to a specific MAGI (`admin` = operator; `assigned` = the person
  being served; `user` = unbound org member; `guest` = external).
  These are per-(MAGI, User) facts; in v0 a User row has a single
  `magis_id` FK (the MAGI it relates to).

All the previous intermediate tables (`employees`, `agents`,
`agent_assignments`, `departments`) are dropped. `contact_entries`
is renamed to `users`.

**Trigger**: next schema-touching change (C1.3 Alembic baseline is
the natural landing site; if Alembic is deferred, the migration
sits behind a `_run_inline_migrations` pass).

#### Concept boundary: org position vs service role

Two independent axes that earlier drafts conflated:

| Axis | Question it answers | Stored in | Values |
|---|---|---|---|
| **Org position** | "What is this agent's role in the MAGIS's structure?" | `magis.position` | `adam` / `eve` |
| **Service role** | "How does this person relate to a specific MAGI?" | `users.role` | `admin` / `assigned` / `user` / `guest` |
| **Service binding** | "Which MAGI does this `users` row belong to?" | `users.magi_id` (FK) | nullable (NULL for `user`/`guest`) |
| **Channel identity** | "Which TG chat / Slack channel does this MAGI own?" | `magi_im_bindings(magi_id, channel, im_id)` | per-(MAGI, channel) row |

These four are independent. Changing `magis.position` does NOT
change `users.role`; changing `users.role` does NOT change
`magis.position`. ADAM is a position; `admin` is a role.

#### Target schema

```python
class MAGIS(Base):
    """一群 MAGI 组成的 Agentic Society。

    一个 MAGIS 不是单个容器 — 它是组织树里的容器节点,持有一个
    Adam container + N Eve containers (每个 MAGIC agent 跑在
    自己的 Pod 里)。MAGISes 通过 parent_id self-FK 形成树结构。
    """
    __tablename__ = "magics"
    id            : int            # PK
    name          : str            # "MAGIS.root" / "MAGIS.acme" / ...
    display_name  : str | None
    created_at    : datetime
    updated_at    : datetime

    members: Mapped[list["MAGIC"]] = relationship(back_populates="magis")


class MAGIC(Base):
    """一个 MAGI 运行时 agent（一个 MAGI Citizen）。

    每个 MAGIC 在它所在的 MAGIS 里持有一个 position：
      - position='adam' → 这个 MAGIS 的 ADAM（leader / operator）。
                         每个 MAGIS 恰好一个（partial UNIQUE）。
      - position='eve'  → 这个 MAGIS 的 EVE（普通 member）。N 个。
    """
    __tablename__ = "magis"
    id            : int            # PK
    name          : str
    display_name  : str | None
    magic_id      : int   # FK → magics.id (ON DELETE CASCADE)
    position      : str   # "adam" | "eve"
    provider      : str | None    # LLM provider (per-MAGI)
    api_key       : str | None    # LLM key (per-MAGI)
    separated_at  : datetime | None
    created_at    : datetime
    updated_at    : datetime

    magic: Mapped["Magic"] = relationship(back_populates="members")


class User(Base):
    """一个人（MAGI 认识的人）。从 contact_entries 改名而来。

    role 是这个人的服务角色（与 MAGI 无关的"4 角色"整体）：
      - 'admin'    : 这个 MAGI 的操作员（能登录它的控制台）
      - 'assigned' : 这个 MAGI 服务的那个人
      - 'user'     : org 成员但未与任何 MAGI 绑定
      - 'guest'    : 外部 / 未知
    """
    __tablename__ = "users"                 # was: contact_entries
    id            : int            # PK
    name          : str
    display_name  : str | None
    role          : str            # "admin" | "assigned" | "user" | "guest"
    magi_id       : int | None     # FK → magis.id
                                   # 非 NULL：role='admin'/'assigned' 时绑到具体 MAGI
                                   # NULL：   role='user'/'guest' 时无 MAGI 绑定
    notes         : str            # free-form markdown（原 ContactEntry.notes）
    source        : str            # "manual" / "eve" / "system"
    last_seen_at  : datetime
    created_at    : datetime
    updated_at    : datetime
```

Plus one partial unique index:

```sql
CREATE UNIQUE INDEX ux_magis_magic_adam
    ON magis(magic_id)
    WHERE position = 'adam';
```

And a `UniqueConstraint` on `users`:

```sql
-- v0: one binding per (User, Magi) for admin/assigned roles.
-- For role='user'/'guest' the magi_id is NULL and the constraint
-- is trivially satisfied.
CREATE UNIQUE INDEX ux_users_role_binding
    ON users(magi_id, role)
    WHERE magi_id IS NOT NULL;
```

(The exact index shape is up to the migration; the invariant we
need: a given `(magi_id, role)` pair has at most one User row when
`magi_id IS NOT NULL`. For M:N in a future v0.x, drop this index
and replace with a `user_magi_bindings` junction table.)

Channel identity lives in its own table (renamed from
`user_im_bindings` to reflect that it binds **MAGIs**, not Users,
to channel identities):

```python
class MagiImBinding(Base):
    """Per-MAGI per-channel IM identity binding.

    Tells the dispatcher "MAGI <magi_id> owns IM id <im_id> on
    channel <channel>". Inbound from that IM id routes to that
    MAGI's runtime. The binding is on the **MAGI side**, not the
    User side.
    """
    __tablename__ = "magi_im_bindings"      # was: user_im_bindings
    magi_id   : int   # FK → magis.id  (was: uid; ON DELETE CASCADE)
    channel   : str   # "tg" / "slack" / "email" / ...
    im_id     : str   # the per-channel IM identifier
    UniqueConstraint(magi_id, channel)
```

#### What disappears

- `employees` table — replaced by `users`.
- `users` table (the proposed earlier F1 rename of `employees`) — replaced by the new `users` (from `contact_entries`).
- `agents` table — never shipped; not needed. Runtime processes bind
  to `magis` rows; process state is runtime-local, not DB.
- `agent_assignments` table — never shipped; not needed.
- `departments` table — gone. The org structure is now "one MAGIC
  + 1 ADAM + N EVE", with no sub-org tree.
- `agents.department_id` column — gone.
- `users.department_id` column — gone.
- `archetype=manager` / `archetype=worker` — gone. Replaced by
  `magis.position` (`adam` / `eve`).

#### What changes semantically

| Old concept | New concept |
|---|---|
| `Employee.role` (admin/assigned/employee/guest) | **`users.role`** (4-value enum, unchanged in values) |
| "Adam" / "EVE" = MAGI runtime archetypes | **"ADAM" / "EVE" = `magis.position`** |
| `agents` table (MAGI process rows) | **`magis`** table (now also holds the MAGI agent itself, not the person) |
| `agents.parent_id` self-FK (org tree) | gone (no org tree; org = MAGIC) |
| `user_im_bindings` (uid-based) | **`magi_im_bindings`** (magi_id-based; binding on the MAGI side) |
| `contact_entries` (per-MAGI contact list) | **`users`** (renamed; the 4-role enum takes over) |
| "Manager ↔ Worker" authority | **ADAM is the leader of this MAGIC** (partial UNIQUE) |
| Manager ↔ Worker = different runtimes | **All MAGI processes are the same runtime**, parameterised by `magis.position` |

#### Cardinality rules

- **1 MAGIC** → **exactly 1 ADAM** + **N EVE** (N ≥ 0). Enforced by
  `UNIQUE(magic_id) WHERE position='adam'`.
- **Many MAGICs** possible (multi-tenant). Each has its own 1 ADAM +
  N EVE.
- A **Magi** belongs to exactly one MAGIC (`magic_id` is a single
  FK).
- A **MAGI process** binds to exactly one Magi.
- **Service bindings** (`users`):
  - v0: a `users` row references at most one Magi via `magi_id`.
    For M:N (one user bound to multiple MAGIs), add a
    `user_magi_bindings` junction in a future v0.x.
  - `users.magi_id` is non-NULL for `role IN {'admin', 'assigned'}`;
    NULL for `role IN {'user', 'guest'}`.
- **Channel identities** (`magi_im_bindings`): a Magi can have
  multiple IM bindings (TG + Slack + Email). One binding per
  `(magi_id, channel)` pair.

#### What renames (column- and table-level)

| Old | New | Reason |
|---|---|---|
| `chat_sessions.uid` | `chat_sessions.magi_id` | FK target renamed; semantics unchanged (still "the person the chat is about") |
| `action_items.uid` | `action_items.magi_id` | same |
| `token_usage.uid` | `token_usage.magi_id` | same |
| `contact_entries` | **`users`** | per-MAGI contact directory becomes the global users table; 4-role enum applies |
| `contact_entries.owner_id` | dropped | `users` is global (FK via `magi_id`); the owner/observed split is gone |
| `contact_entries.person_id` | dropped | same; a `users` row IS the person |
| `user_im_bindings` | **`magi_im_bindings`** | binding is on the MAGI side, not the user side |
| `user_im_bindings.uid` | `magi_im_bindings.magi_id` | same |
| `ToolContext.uid` | `ToolContext.magi_id` | runtime cookie identity = `magi_id` |
| `magi_session` cookie value | unchanged (it's just an int) | value is `magi_id` now; old cookies carrying `contact.id` need re-login |
| `MAGI_NODE_ROLE=adam/eve` | `MAGI_NODE_ROLE=adam/eve` (kept) | docstring updated to "position selector" — env name stays for back-compat |

#### What changes code-wise

| Surface | Action |
|---|---|
| `magi/agent/db/models_employee.py` → split into `models_magic.py` + `models_magi.py` + `models_user.py` + `models_magi_im_binding.py` | new files; old `models_employee.py` dropped |
| `magi/agent/memory/contacts/models.py` → becomes `magi/agent/db/models_user.py` | rename + add `role` + `magi_id` columns; drop `owner_id` / `person_id` |
| `Employee` class | gone |
| `ContactEntry` class | renamed to `User`; restructured per above |
| `Magi` class | new; columns above |
| `Magic` class | new; columns above |
| `MagiImBinding` class | new; replaces `UserImBinding` |
| `departments` table + `Department` model | dropped |
| `agents` / `agent_assignments` (proposed in earlier F1 drafts) | dropped (never shipped) |
| `Employee.provider` / `Employee.api_key` | move to `Magi` |
| `loop.py` LLM provider resolution | reads `Magi.provider` / `Magi.api_key` via the bound Magi row |
| WebUI sidebar / tree view | a tree of **MAGICs** → expand a MAGIC → list its members (`magis`) with positions; expand a Magi → list its `users` (with `role`) |
| `/api/departments/*` | drop entirely |
| `/api/employees/*` | drop entirely (no alias; it never shipped clean) |
| `/api/magis/*` | MAGIS list / create / update / delete |
| `/api/magic/*` | MAGIC Citizen list / create / update / delete; `magic_position` is required |
| `/api/users/*` | new; list / create / update / archive User rows; `role` is a required field; `magi_id` is required iff `role IN {'admin','assigned'}` |
| `/api/eves/*` | now a view onto `magis` filtered by `position='eve'` |
| `dispatcher.lookup_im_id` | reads `magi_im_bindings.magi_id`; the lookup's "owner of this IM id" is a Magi, not a User |
| EVE ↔ Adam dispatch RPC | resolved by reading the bound Magi row's `magic_id` (parent MAGIC) and that MAGIC's ADAM-position Magi |
| Runtime MAGI process boot | reads `MAGI_NODE_ROLE` env (still the position selector); looks up the corresponding `Magi` row in DB by IM binding; verifies `position` matches the env; loads position-specific policy from there |

#### Migration order (dev, 1 Alembic revision; prod, multi-step)

For dev (zero prod data), this collapses to a single Alembic revision
that does the whole thing in one transaction:

1. Create `magics` table.
2. Create `magis` table + partial UNIQUE index `ux_magis_magic_adam`.
3. Rename `contact_entries` → `users`; restructure columns
   (drop `owner_id` / `person_id`; add `role` + `magi_id`).
4. Rename `user_im_bindings` → `magi_im_bindings`; rename `uid` →
   `magi_id`.
5. Seed one default MAGIC (`name='MAGIC.root'`).
6. Backfill `magis`:
   - From each `Employee.role='admin'` row → create a `Magi` with
     `position='adam'` under the default MAGIC.
   - From each `Employee.role='assigned'` row → create a `Magi`
     with `position='eve'` under the default MAGIC.
7. Backfill `users`:
   - From each `Employee.role='admin'` row → create a `User` with
     `role='admin'`, `magi_id=<the Adam magi row's id>`.
   - From each `Employee.role='assigned'` row → create a `User` with
     `role='assigned'`, `magi_id=<the matching EVE magi row's id>`.
   - From each `Employee.role='employee'` row → create a `User` with
     `role='user'`, `magi_id=NULL`.
   - From each `Employee.role='guest'` row → create a `User` with
     `role='guest'`, `magi_id=NULL`.
   - Migrate `ContactEntry.notes` → `User.notes` (one row per
     `ContactEntry`; each `User` row may carry multiple notes
     — see Open Question 13).
8. Move `provider` / `api_key` from old `Employee` to new `Magi`.
9. Rename `chat_sessions.uid` / `action_items.uid` /
   `token_usage.uid` → `*_magi_id`.
10. **Drop** `employees` / `departments` tables.
11. Add `MAGIC`-aware seed helpers (replaces `_seed_default_root`
    which seeded the old "MAGI.org" department).

For prod, steps 1–11 split across two releases (additive first,
then the rename + drop). The unique-index guarantees on ADAM and
on `(magi_id, role)` let the additive phase run without constraint
violations.

#### What does NOT change

- `MAGI_NODE_ROLE` env var name (`adam` / `eve`) — kept for
  back-compat. Docstring updated to "MAGI process position
  selector". The Magi row's `position` column should match (a
  sanity check on boot).
- The cookie identity model — still "an int in `magi_session`";
  the int now refers to a `magis.id`.
- D.28 channel dispatcher surface — `user_im_bindings` is renamed
  to `magi_im_bindings` but its query shape is unchanged.
- Chat session scope — D.22 cross-channel write guard, D.23 session
  identity keyed by Magi id, D.24 cookie identity by Magi id — all
  remain semantically identical.
- Single DB per workspace (default SQLite). The MAGIC's DB holds
  the canonical `magics` + `magis` rows; each Magi's own DB (if
  split) holds its chat state. (Postgres shared across MAGIs is
  the upgrade path; schema is unchanged.)

### F2 — Archetype + archetype-aware code paths

**Trigger**: needs a new archetype (e.g. project-MAGI) or
the operator wants archetype-named columns / settings.

| Code surface | Action | Trigger / ETA |
|---|---|---|
| `MAGI_NODE_ROLE` | keep `adam` / `eve`; add docstring "manager / worker archetype" mapping | **Done in doc; code docstring next** |
| `MAGI_NODE_ROLE` future values | register new archetype by adding a tuple to `VALID_ROLES` + a `scope` policy entry | when third archetype ships |
| Per-MAGI SOUL.md location | `workspace/<magi_id>/SOUL.md` (was `workspace/Adam/SOUL.md` — bundled default) | C4 |
| `eve-<id>` Docker service naming | rename to `magi-<id>` when next dispatch lands | C6 dispatch PR |

### F3 — i18n + copy cleanups

**Trigger**: next time `messages.ts` is touched for a
feature.

| Surface | Action |
|---|---|
| `knowledgeContactsIntro` / `knowledgeMemoryIntro` / `tgReactionsDesc` / `roleAssistant` / `employees` / `tasksHint` / `newChatHint` / `searchHint` | review and replace "员工 / assigned employee" wording with "User / assigned User"; replace "EVE 是 Everyday Virtual Employee" with "EVE 是 worker-archetype MAGI" |
| Persona / onboarding copy | reflect new archetype language |

### F4 — Glossary + module docstrings

**Trigger**: next module docstring pass.

| Surface | Action |
|---|---|
| `magi/__init__.py` | update module docstring to the new framing (done 2026-07-23) |
| `magi/node/__init__.py` | update docstring to talk about archetype, not "Adam vs EVE" |
| `magi/agent/db/models_employee.py` | replaced by F1's `models_user.py` + `models_agent.py` + `models_agent_assignment.py` |

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
  gate by channel — same User can browse TG
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

### D.23 — Session identity keyed by `User.id`

- `SessionStore` first arg is `uid: int`,
  not `tgid: str`. `tgid` is now keyword-only
  on `create()` and stamps the per-channel delivery
  address on the row's `tgid` column. This lets the
  same User own sessions across channels (TG +
  WebUI) with a single identity.
- Read scope: anything whose `uid` matches
  the caller (cross-channel by design — see Open
  Question 7).
- Write scope: cross-channel writes raise
  `ChannelMismatchError` (D.22).

### D.24 — Cookie identity by `User.id`

- `magi_session` cookie value is the uid (User PK,
  was `str(telegram_id)`). `OperatorGate` reads by primary
  key. The login flow's `_resolve_uid_for_tgid()` helper
  translates a TG tgid → uid before `verify_login_code`
  sets the cookie. (Pre-D.27 this helper was named
  `_uid_for_tgid`; the rename from the older helper is cosmetic —
  the resolution shape is identical.)
- `/api/auth/me` returns `{uid, telegram_id,
  display_name, is_super_operator}` — the operator's
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
  rendered with the User's real
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
  per-block templates in `context/`:
  - `context/memory_block.md` — header + intro +
    per-kind sub-section headings
    (`### 重要的事`, `### 正在进行`)
  - `context/contact_block.md` — `## Current chatter`
    header + intro
  - `context/skills_block.md` — `## Available skills`
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

### D.28 — Channel dispatcher (adapter pattern)

Architecture: `magi/channels/dispatcher.py` is the ONE dispatch point for
domain code. Each channel implements a `ChannelAdapter` Protocol (send /
lookup_im_id / bind_im_id / unbind_im_id) and registers into a process-global
registry. Domain code (tools, runner, webui api auth) talks only to the
dispatcher; it never imports a channel adapter directly or knows about TG
chat ids.

```
domain code (tools, runner, webui api)  →  dispatcher  →  adapter (TG/Slack/...)
               uid + channel + session_id                    owns per-channel IM id
```

**Completed:**

- `channels/dispatcher.py` — `send_to_session`, `send_to_uid`, `lookup_im_id`,
  `bind_im_id`, `list_bindings`, `list_channels`.
- `channels/telegram/adapter.py` — `TelegramAdapter` implements
  `ChannelAdapter`. `send()` goes through raw HTTP (`send_text_auto`) to avoid
  the loop-bound `bot.send_message` bug. Auto-registers at import time.
- `chat_sessions.tgid` → `delivery_address` column rename (migration entry in
  `_RENAME_COLUMN_MIGRATIONS`, data survives — SQLite rename is metadata-only).
- `agent/tools/send_message.py` → `dispatcher.send_to_session`.
- `agent/tools/schedule_task.py` → reads `session.delivery_address`.
- `agent/proactive/runner.py` → `_tg_send_callback` closure removed; calls
  `dispatcher.send_to_session`.
- `agent/loop.py` — zero `tgid` references.
- `auto_title.py` — `delivery_address` instead of `tgid` in `TitleJob`.
- `chat_sessions.py` / `chat_search.py` — schemas use `delivery_address`.
- WebUI path: dispatcher appends directly to the session store (no adapter
  needed for WebUI — the user sees the message inline).

**Remaining stragglers** (tgid still appears outside `channels/telegram/`):

- `channels/webui/api/onboarding.py` — Pydantic schemas still have `tgid`
  field names; error messages say "tgid must be numeric".
- `agent/memory/contacts/store.py` — `find_by_telegram_id(tgid)` parameter name.
- `agent/memory/session/ids.py` — `_validate_tgid`, `session_lock(tgid, ...)`.
- `agent/memory/session/migration.py` — legacy JSON migration uses `tgid` var.
- `agent/memory/session/models.py` — `d["tgid"]` backward-compat deserialization.

**D.29 follow-up** (``magi_im_bindings`` table): moves `Contact.telegram_id`
into a proper `(magi_id, channel, im_id)` table and drops the denormalised
column. Deferred to the F1 three-table migration.

## Open questions for the user

These show up while reading the code but the code
itself is silent on which direction to go. Worth
asking before sinking more time:

1. **C1.3 Alembic migration** — **Resolved.** A real Alembic
   baseline (`0001_baseline`) shipped; the legacy inline pass
   (`_run_inline_migrations`) now runs only as the one-time
   adoption step for pre-Alembic DBs (stamped to `0001_baseline`,
   then Alembic owns every change). Current chain:
   `0001_baseline → 0002_admin_role_split → 0006_contact_notes`.
   See [docs/database-migrations.md](database-migrations.md).
2. **C2 self-serve `/start <code>`** — code-generated
   one-time codes (operator prints), or QR-coded
   deep link from the WebUI? Comment says "code
   flow that uses the right thing" without
   specifying.
3. **C4 per-MAGI SOUL.md** — stored as a row in
   the DB (new `magi_soul` table) or as a file
   under `<workspace>/magis/<id>/SOUL.md`?
4. **C7 WebSocket payload** — what fields go in each
   frame? (token deltas? tool calls? raw blocks?)
5. **C8 `MAGI_SECRET` distribution** — how does the
   deployer get the secret into the container?
   File-mounted? Env var? Vault? The encryption
   code needs an answer before the rollout.
6. **D.24 cookie compatibility** — old cookies stored
   `str(telegram_id)`; the new `str(user.id)`
   breaks existing sessions on upgrade. For dev this
   is fine, but pre-production deploys need a
   migration path (force re-login, or transparently
   re-resolve `tgid → user.id` on first request
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
9. **Post-refactor schema migration (F1, three tables)** —
   bundled into one Alembic revision, or split across
   additive + rename PRs? Bundle is safer (single
   transaction; partial-UNIQUE guarantees the ADAM
   invariant holds mid-migration); split is reviewable.
   See F1's "Migration order" section for the 11-step
   ordering.
10. **Third archetype** — when (if ever) does a
    non-manager / non-worker archetype ship? The
    runtime is ready (`VALID_ROLES` is just a tuple);
    the work is purely in archetype-aware policy +
    dispatch UX. Worth deciding before C6 / C7.
    *(Note: with the F1 reframe, "archetype" is now
    `magis.position`. Adding a third position is the
    same shape of work — extend the enum + the partial
    UNIQUE rule if needed.)*
11. **ContactEntry.notes → User.notes migration** —
    `ContactEntry` rows today have free-form `notes`
    markdown scoped to a (Magi, person) pair. When
    `contact_entries` is renamed to `users`, a single
    User row may have multiple `ContactEntry.notes`
    rows pointing at it (one per Magi that knew them).
    Three options:
    (a) concatenate notes into a single `users.notes`
        field (lossy; loses the per-Magi provenance);
    (b) keep `contact_entries` as a separate notes
        table FK'd to `users.id`, leaving notes
        per-binding;
    (c) drop the per-binding structure entirely and
        just have one `notes` per User (assume most
        rows have a single Magi anyway).
    Worth deciding before the C1.3 Alembic baseline,
    because the answer affects whether
    `contact_entries` survives as its own table.

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
- [database-migrations.md](database-migrations.md) — the canonical
  migration chain, startup behaviour, and the discipline for adding
  new schema changes

# MAGI — Roadmap (C0 → C8)

The project ships in numbered checkpoints (**C0** … **C8**),
each a self-contained deployable slice. Smaller increments
inside a checkpoint (e.g. D.0, D.6, D.17, D.18) are drops
and tracked in the changelog / commit history, not in this
file.

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

### C1.3 — Alembic baseline + WebUI completion

| Item | Status | Notes |
|---|---|---|
| First Alembic baseline migration (replaces `migrations.py` `_run_inline_migrations`) | **Next** | Multiple comments call this out: "end of C1.3" |
| All remaining C1.1 routes: `/api/eves`, `/api/skills`, `/api/audit`, `/api/login` | **Next** | `app.py` lists them under "C1.2 — more routers" |
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

**Not in C2 (deferred):**

- Per-employee SOUL.md — `loop.py: "C4 will move this
  to a per-employee"`. Currently `SOUL.md` is
  workspace-global.
- Cross-employee chat routing (C6+) — see C6.

---

## C3 — Cross-channel dispatcher + audit ingest

The slice where EVE and Adam are distinct node roles
that talk to each other.

| Item | Status | Notes |
|---|---|---|
| Real agent-loop dispatcher (replace C0 first-touch handler) | **Next** | `node/__init__.py: "C3 will replace this with the real agent-loop dispatcher"` |
| Multi-channel asyncio.gather for the runtime | **Next** | `node/__init__.py: "in C3 once the Telegram runtime exists (asyncio.gather)"` |
| `/ingest/audit` route (EVE → Adam) | **Next** | `app.py: "C3 — /ingest/audit, /ingest/heartbeat"` |
| `/ingest/heartbeat` route (EVE → Adam) | **Next** | Same |
| Adam ↔ EVE auth via `MAGI_SHARED_SECRET` | **Done** | `NodeConfig` knows the env vars; HTTP client + server impl lands in C3 |
| Per-employee LLM provider routing (assigned → own key) | **Partial** | `Employee.provider` + `Employee.api_key` exist; C3 is when the dispatcher actually wires them per-employee |
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
| Memory subsystem fully wired into `loop.py` prompt assembly | **Next** | Format functions exist; `loop.py` doesn't call them yet — see the "What's next" section below |
| Memory management UI in WebUI (operator sees / edits / deletes rows) | **Next** | Currently the table is LLM-only; no `/api/memory` route |
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
| `tokens.py` to `llm/` (DONE) | n/a | Done in this refactor series |

---

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
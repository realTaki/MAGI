# MAGI — Modular Agentic Governed Intelligence

A localized enterprise agent system where every employee gets a dedicated
**EVE** (*Everyday Virtual Employee*) — a personal agent that handles daily
communication, information organization, reminders, follow-ups and process
push, all over Telegram.

The product is **not** a SaaS chatbot and **not** a code-writing tool. It
runs on-premise, one **Adam** node (Web-frontend control / orchestration
backend for HR) + one Docker container per employee (EVE), with strict
governance (audit, RBAC, hash-chained logs) baked in from day one.

> **⚠️ This project is entirely written and maintained by AI.** It is in an
> early experimental stage and may contain bugs, incomplete features, or
> incorrect behaviours. Use at your own risk in production or production-like
> environments. Contributions and bug reports are welcome.

## Naming and architecture

| Name       | Role                                                                                  |
|------------|---------------------------------------------------------------------------------------|
| **MAGI**   | The whole system.                                                                     |
| **Adam**   | The enterprise-side node. Provides a **Web frontend** for HR / admin to operate everything (employees, skill registry, EVE dispatch / recall, audit, status). Default channel: **WebUI**. |
| **EVE**    | The employee-side node, one per employee. Default channel: **Telegram**. Pulls enterprise-wide data (directory, settings, enterprise skills) from Adam and caches locally. |
| *admin*    | The user role (HR / IT) that uses Adam's Web UI. Kept lowercase on purpose.           |

**Adam and EVE are the same node.** There is one `magiruntime` package (agent loop, dynamic context, skill runner, proactive engine, LLM provider, audit) and one process image. Every architectural choice is an independent config axis — no axis is hardcoded by role:

| Axis             | Env var                  | Default by role          | Notes                                                              |
|------------------|--------------------------|--------------------------|--------------------------------------------------------------------|
| Permission scope | `MAGI_NODE_ROLE`         | `adam` = enterprise, `eve` = personal | The **only** thing that role selects. Affects the policy gate inside the runtime. |
| Channels         | `MAGI_CHANNELS`          | `adam` → `webui`, `eve` → `telegram`  | Comma-separated list. Adam can mount Telegram too; EVE can mount WebUI too. |
| State backend    | `MAGI_STATE_BACKEND`     | `auto` (postgres if `DATABASE_URL` set, else sqlite) | Independent of role. EVE can use Postgres if a shared store is desired; Adam can use SQLite for a dev install. |
| Adam peer        | `MAGI_ADAM_URL`          | `http://adam:8000`       | Always read. Any node that needs Adam's RPC (audit, config pull) sets this. |
| LLM provider     | `ANTHROPIC_API_KEY` etc. | unset                    | Per-node or global.                                                 |

The role just sets permission scope and a couple of default fields; every underlying axis is overridable. `magi.node.run()` does not branch on `role` — it iterates the channel list and hands off to each channel's launcher.

> Full architecture, deployment topology, RPC contract and Phase 1 build
> order live in [`.claude/plans/linked-cooking-waffle.md`](.claude/plans/linked-cooking-waffle.md).
> This README only covers what you need to run the code.

## Scope (explicit constraints)

- **No CLI.** All operator / management work is done in Adam's Web UI. The Docker orchestration behind dispatch / recall is invisible to operators.
- **EVE instances do not talk to each other.** Each EVE only talks to Adam and to its own employee over Telegram. Any cross-employee coordination lives in Adam.
- **WebUI is just another channel.** It's the `channels/webui/` adapter; Telegram is the `channels/telegram/` adapter. Both implement the same `Channel` interface and deliver messages into the same runtime agent loop.

---

## Repository layout

Flat layout — packages live at the repo root, no `src/` wrapper.

```
magi/
├── __init__.py
├── __main__.py     # Single entry point. Validates MAGI_NODE_ROLE, dispatches to magi.node.
├── runtime/        # Shared core: agent loop, context, skills, proactive, LLM, audit.
│                   # Adam and EVE run the same runtime; only channel + scope + state differ.
├── channels/       # Pluggable channel adapters. Either role can mount any subset.
│   ├── base.py     # Channel Protocol — both adapters implement this.
│   ├── telegram/   # python-telegram-bot v21+ (C3+).
│   └── webui/      # FastAPI + HTMX (CRUD) + WS (chat console, C7+).
│       └── app.py  # The FastAPI app; built lazily by `webui` launcher.
└── node/           # Node assembly: one NodeConfig, one check(), one run().
    └── __init__.py # No role-based code paths. Iterates MAGI_CHANNELS, launches each.
tests/              # unit / integration / e2e (one e2e file per checkpoint).
```

One console script:

| Script  | Role                                                                                                                       |
|---------|----------------------------------------------------------------------------------------------------------------------------|
| `magi`  | Boots a MAGI node. `MAGI_NODE_ROLE` chooses the permission-scope preset; `MAGI_CHANNELS`, `MAGI_STATE_BACKEND` etc. override per-axis defaults. |

---

## Quick start (local dev, Phase C0)

Phase C0 only validates that the project structure, the single entry
point and Adam's `/health` endpoint work. Real features (employees, TG
bots, LLM calls, audit, dispatch UI) land in subsequent checkpoints.

### Prerequisites
- Python ≥ 3.12
- [`uv`](https://docs.astral.sh/uv/) ≥ 0.11

### Install
```bash
uv sync --extra adam --extra eve
```

### Run a node (choose the role at runtime)
```bash
# EVE (stub) — print resolved config and exit
MAGI_NODE_ROLE=eve uv run magi --check

# Adam — boot FastAPI on :8000
MAGI_NODE_ROLE=adam uv run magi
# in another shell:
curl http://127.0.0.1:8000/health
# → {"status":"ok","service":"magi","version":"0.1.0"}
```

### Run with Docker Compose (full local stack)
```bash
cp .env.example .env
# edit MAGI_SHARED_SECRET and any LLM provider keys you want to enable
docker compose up --build
# Adam at http://localhost:8000/health
# Postgres at localhost:5432 (user/pass: magi/magi, db: magi)
```

The compose file currently runs `postgres` + `adam` only. Per-employee
`eve-<id>` services are wired up in checkpoint C6 alongside the dispatch
button in Adam's Web UI — both build from the same Dockerfile and
differ only via `MAGI_NODE_ROLE`.

---

## Phase 1 roadmap

Nine demoable checkpoints (≈ four weeks for a small team):

| #  | Checkpoint                                            | Demo                                |
|----|-------------------------------------------------------|-------------------------------------|
| C0 | Skeleton — uv project, single entry point            | `curl /health` → 200                |
| C1 | Employee / EVE / Skill registry on Adam WebUI         | create / edit / delete in browser   |
| C2 | Telegram ID binding via one-time code                 | send code from a real TG account    |
| C3 | Channel abstraction + TG channel + config pull        | real chat round-trip                |
| C4 | Skill loader + 4 MVP skills (scope-aware)             | "remind me at 3pm", "search KB"     |
| C5 | Proactive reminders (APScheduler + engine)            | reminder fires + audit              |
| C6 | Dispatch / recall via Adam Web UI (docker SDK)        | bring up / tear down an EVE         |
| C7 | Control console (chat-style SPA via WebUI channel)    | live event stream                   |
| C8 | Hardening — hash chain, snapshot, outbox cap          | kill Adam, EVE keeps going          |

See the plan file for the full checklist per checkpoint.

---

## Governance notes

MAGI treats audit as a first-class concern: every channel message in/out
(regardless of which channel — WebUI or Telegram), every skill call and
every admin action lands in `audit_log` (immutable, hash-chained) or
`event_log` (high-cardinality, TTL'd). The skill execution boundary is
JSON-in / JSON-out from day one so sandboxing can be tightened in later
phases without a refactor. EVE containers cache their config locally and
run in degraded mode when Adam is unreachable — local deployment means
Adam reboots are common, not exceptional.
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
| Adam peer        | `MAGI_ADAM_URL`          | `http://adam:42069`      | Always read. Any node that needs Adam's RPC (audit, config pull) sets this. |
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

Every MAGI container ships both the Python backend and the Web frontend
together, so the layout reflects that — frontend lives **inside**
`magi/` as a sibling sub-tree.

```
magi/                          # Every MAGI node (Adam or EVE) — agent + WebUI in one
├── __init__.py                # Python package marker (the "agent")
├── __main__.py                # Single entry point. Validates MAGI_NODE_ROLE, dispatches to magi.node.
├── runtime/                   # Shared core: agent loop, context, skills, proactive, LLM, audit.
│                              # Adam and EVE run the same runtime; only channel + scope + state differ.
├── channels/                  # Pluggable channel adapters. Either role can mount any subset.
│   ├── base.py                # Channel Protocol — both adapters implement this.
│   ├── telegram/              # python-telegram-bot v21+ (C3+).
│   └── webui/                 # FastAPI app; lazily mounted when the `webui` channel is enabled.
├── node/                      # Node assembly: one NodeConfig, one check(), one run().
│   └── __init__.py            # No role-based code paths. Iterates MAGI_CHANNELS, launches each.
└── WebUI/                     # React SPA (single Vite app, lives inside the magi/ folder)
    ├── package.json
    ├── tsconfig.json
    ├── .nvmrc
    ├── index.html
    ├── vite.config.ts
    └── src/

tests/                         # unit / integration / e2e (one e2e file per checkpoint)
```

### Frontend stack (lands in C1.0b)

React 19 + TypeScript 5 + Vite 5 + Tailwind CSS v4 + shadcn/ui.
Routing: **React Router v6** (no TanStack Router — keep deps minimal).
Server state: **TanStack Query**. Forms: **react-hook-form + zod**.
Types over the wire: **openapi-typescript** regenerates `magi/WebUI/src/api/types.gen.ts`
from FastAPI's `/openapi.json` (`npm run generate-types`). Tests: **Vitest**.

Adam and the console share one React tree (admin lives at `/admin/*`,
console at `/console/*` once C7 lands). Built artifact
(`magi/WebUI/dist/`) is baked into the Adam image via a multi-stage
Dockerfile in **C1.4**.

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

# Adam — boot FastAPI on :42069
MAGI_NODE_ROLE=adam uv run magi
# in another shell:
curl http://127.0.0.1:42069/health
# → {"status":"ok","service":"magi","version":"0.1.0"}
```

### Run with Docker Compose (Adam only, C0)
```bash
cp .env.example .env
# edit MAGI_SHARED_SECRET (only required var)
docker compose up --build
# Adam at http://localhost:42069/health
```

The compose file currently runs **just the Adam container** — no
Postgres, no EVE. Postgres is added back in **C1** (when the ORM
lands); per-employee EVE containers are wired up in **C6** via Adam's
Web UI dispatch button.

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
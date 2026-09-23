# MAGI — Modular Agentic Governed Intelligences

[![License](https://img.shields.io/badge/license-BUSL--1.1-blue.svg)](LICENSE)
[![TypeScript](https://img.shields.io/badge/runtime-TypeScript%20%2B%20Bun-blue)](https://bun.sh)
[![Status](https://img.shields.io/badge/status-experimental-orange)](#project-status)

[中文 README](README_zh.md)

> **MAGI is a runtime for persistent, modular, governable agent societies.**
>
> A MAGIS is a **MAGI Society** — a persistent organization of independent MAGI.
> Each MAGI has its own runtime, workspace, memory, tools, provider credentials,
> and role in the Society.
> They coordinate through the Society, execute through independently managed
> MAGI runtimes, retain what they learn, and grow into a durable collective
> intelligence without giving up boundaries, accountability, or operator control.

MAGI is built for the question beyond “how do I delegate this task?”:

**How do we give a group of AI agents identity, continuity, organization, and
the freedom to improve together over time — while keeping that autonomy
observable, bounded, and governable?**

## Why MAGI?

Most multi-agent systems assemble a temporary team around a workflow: assign a
research task, collect a result, then tear the team down. MAGI treats the
**organization itself** as the primary unit.

| Task-oriented multi-agent orchestration | MAGI Society runtime |
| --- | --- |
| Agents are steps in a workflow | MAGI are persistent members of an organization |
| Collaboration ends with a task | Context, memory, skills, and relationships persist |
| One process commonly hosts many agents | Every MAGI has an independent runtime and workspace |
| A controller defines the execution path | The Society coordinates agents while infrastructure enforces lifecycle and boundaries |
| Scale means adding concurrent calls | Scale means adding capable MAGI and connected Societies |

MAGI does not replace workflow engines. It provides a substrate for long-lived
agent organizations that can operate, learn, reorganize, and eventually
coordinate more of their own work.

## Design philosophy

MAGI is designed for a future in which **intelligence becomes cheaper and more
abundant**, while **coordination, trust, security, and governance remain hard**.

That leads to four principles:

- **Do not hard-code around temporary model limitations.** Token cost, context
  size, and reasoning quality will change quickly; the architecture should not
  depend on them staying scarce.
- **Prefer protocol-mediated coordination over rigid workflow control.** As
  agents become more capable, infrastructure should increasingly define how
  agents discover, communicate, and delegate — not prescribe every reasoning step.
- **Keep governance mandatory.** Identity, permissions, isolation, observability,
  resource boundaries, and accountability become more important as agents gain
  more autonomy.
- **Make the running system editable.** The desktop app runs MAGI, ASP, and the
  WebUI from a local Git checkout. This gives each installation a source tree
  that can evolve independently as MAGI moves toward recursive self-improvement
  (RSI).

The long-term goal is to build the infrastructure in which autonomous
intelligences can collaborate freely **within explicit, inspectable constraints**.

## Repository layout

```text
shell/       Electron shell: window, checkout, and bundled Node, npm, and Bun
app/         Operator UI and the local backend that starts ASP
asp/         ASP server (TypeScript, node main.ts): /sessions, WS /connect, ~/.magi/asp.sqlite
magi/        MAGI runtime: BUS, workers, tools, and channels
```

The projects are siblings. Each running MAGI uses `magi/`; its BUS mediates
all Worker state and communication.

## Toward governed collective intelligence

A MAGIS should become better because it has existed — while remaining
inspectable and governable:

- MAGI learn from the outcomes, failures, and observations of their work.
- Useful procedures become reusable Skills rather than disappearing into an
  individual conversation.
- ADAM can recognize capability gaps, organize specialized EVAs, and reshape
  the Society as its work changes.
- Societies can share knowledge and collaborate without reducing every member
  to a stateless API call.
- Operators remain able to inspect the organization, its memory, its tools,
  its resource boundaries, and the authority used to change it.

> **Implementation status:** a local desktop, one ASP process, and one Bun
> process per MAGI are what runs today. Each MAGI keeps its own workspace
> (memory, skills, tasks, contacts, prompts) and talks to the operator through
> ASP. A Society tree, an ADAM control plane, and agent-to-agent job boards
> are design goals. They are **not** in this tree.

## The MAGI model

The names are deliberate:

| Term | Meaning |
| --- | --- |
| **MAGI** | The general kind of autonomous, governable agent in this system. |
| **MAGIS** | A **MAGI Society**: an organization of MAGI. Societies form a tree. |
| **MAGIC** | An old internal name for one MAGI. The current runtime does not have a MAGIC table. |
| **ADAM** | The leading MAGI of a Society. ADAM provides its control plane and coordinates its MAGI. |
| **EVA** | A working MAGI role. A Society can create, configure, start, stop, and retire multiple EVAs. |

ADAM and EVA name roles a Society would have. The running app does not yet
model a Society tree or an ADAM control plane. ASP is the local lifecycle
boundary: it starts MAGI processes and relays their sessions. It does not
decide what a MAGI should say.

## What exists today

- **Desktop** — an Electron shell clones this repository to `~/.magi/MAGI`,
  installs `asp/` and `magi/`, builds the operator UI, and starts ASP. The
  package carries Node.js 24, npm, and Bun. It does not carry a Python runtime.
- **ASP** — Node 24, `asp/main.ts`, bound to `127.0.0.1:42069`. It stores
  sessions and relay events in `~/.magi/asp/asp.sqlite`. Creating a bot spawns
  that MAGI; creating a group opens a conversation the operator can invite
  MAGI into.
- **One process per MAGI** — Bun runs `magi/magi.ts`. The default workspace is
  `~/.magi/magi/<name>`. An older `~/.magi/ts-magi/<name>` directory is still
  opened when the new path does not exist.
- **BUS inside each MAGI** — Books for conversations, messages, memory, skills,
  tasks, contacts, prompts, and tools; Jobs for chat, model calls, tool calls,
  delivery, provider changes, tasks, and MCP server changes.
- **Operator data stays on the desktop** — chat history is
  `~/.magi/app/chat.sqlite`. Provider settings and the API key are
  `~/.magi/app/provider.json`. ASP forwards a provider update and does not
  keep a new copy of the key.
- **Other channels** — a MAGI can also talk on the terminal, Telegram, and
  configured MCP servers. Those are part of the MAGI process, not of ASP.

## Quick start

MAGI currently runs as a local ASP service plus local MAGI processes. There is
no root `deploy/` tree.

| Situation | Where | What you run |
| --- | --- | --- |
| Shell | [`shell/`](shell/) | Open the Electron app. It starts local ASP; ASP starts MAGI. |

**Desktop:** on first launch, the app clones the complete repository into
`~/.magi/MAGI`, prepares local dependencies, builds the WebUI, and starts ASP
on [http://127.0.0.1:42069](http://127.0.0.1:42069). A startup page shows
the preparation stages before opening the WebUI, and while the society is still
empty the backend seeds the first three MAGIs — **MELCHIOR**, **BALTHASAR**,
**CASPER**. The app is also the
machine-local layer: connecting the checkout to the operator's GitHub account
(fork plus `origin`) happens in the WebUI after startup, not while booting.
Creating a bot is `POST /conversations { "kind": "bot" }` — ASP assigns
`eva-000` and starts that MAGI.

**One MAGI:** `bun run start -- <handle> <base> <token>` from `magi/`. ASP launches this command with MAGI's bundled Bun runtime.


## From the first launch

1. **Open the desktop app.** The shell clones the repository, installs
   dependencies, builds the UI, and waits until ASP answers on port 42069.
2. **Meet the first three MAGI.** While none exist, the app creates
   `eva-000`, `eva-001`, and `eva-002`, and tries to nickname them
   **MELCHIOR**, **BALTHASAR**, and **CASPER**. A MAGI that never comes online
   stays unnamed.
3. **Talk.** The desktop writes the transcript locally, then sends the message
   through ASP. The MAGI answers on its WebSocket and the desktop stores that
   too.
4. **Set a provider.** Settings writes `~/.magi/app/provider.json`. ASP hands
   the same values to each connected MAGI, which stores them in its own BUS.
5. **Invite.** A group conversation can add a MAGI that ASP already started.
   That MAGI joins when it receives `session.invited`.

## Architecture

```text
Operator
   │
   ▼
shell/            Electron window
   │  loads
app/              operator UI and local backend
   │  starts Node 24
   ▼
asp/              127.0.0.1:42069
   │  spawns Bun
   ├── magi  eva-000     ~/.magi/magi/eva-000
   ├── magi  eva-001     ~/.magi/magi/eva-001
   └── magi  eva-002     ~/.magi/magi/eva-002
```

ASP starts processes and relays session events. It is not the place a MAGI
reasons. Each MAGI keeps its own SQLite workspace. The desktop keeps the
operator's transcript and provider key.

### Desktop UI and ASP

The Electron app starts a local ASP service. ASP owns HTTP, WebSocket
`/connect`, and MAGI process creation; the desktop does not start MAGI
directly. The WebUI is built and loaded from the local checkout.

### Local source and the path to RSI

The packaged app supplies an Electron bootstrap shell and private Git,
Node.js, and Bun tools. On first launch it clones the complete repository
to `~/.magi/MAGI`. Later launches keep that Git working tree and use its
`magi/`, `asp/`, and `app/` sources. Local changes are not
overwritten or pulled automatically. A user or coding agent can edit the
checkout, rebuild the interface, and merge future upstream changes using Git.
Once the operator connects GitHub from the interface, `origin` is their fork and
`upstream` stays the repository the app cloned, so pushes land in their own
account.
When `app/dist/index.html` changes, the app asks before reloading.

Only the bootstrap stays in the installed package: `shell/` clones the
checkout, loads the app from it — interface (`app/src/`) and local
backend (`app/main/`) — and then simply asks that backend to prepare the
checkout, start ASP and name the interface entry, through one generic bridge.
Editing the app takes effect without a new package; editing `shell/`
still does not change the running shell. Code changes in MAGI or ASP also need the affected process
to restart. The editable checkout is the foundation for RSI, **not** an
autonomous self-update system yet: MAGI does not currently validate, activate,
restart, or roll back its own code revisions as one managed operation.
See [shell details](shell/README.md).

For the implementation-level view, see:

- [Architecture](docs/ARCHITECTURE.md)
- [Business flows](docs/business-flows.md)
- [Terms and canonical ID names](docs/terms.md)
- [ASP](asp/README.md)
- [Roadmap](docs/ROADMAP.md)

## Project status

MAGI is experimental and under active construction. What ships is a local
desktop, ASP, and one Bun runtime per MAGI. The Society tree and cross-MAGI
collaboration described above are not implemented in this repository.

The broader vision — autonomous learning, protocol-mediated coordination,
richer policy enforcement, and increasingly self-organizing governed
intelligences — is intentionally public. The README distinguishes that direction
from shipped behavior so MAGI can remain ambitious without confusing roadmap
with implementation.

## Contributing

MAGI is developed by humans and AI collaborators. Contributions and design
discussion are welcome.

1. Read [CONTRIBUTING.md](CONTRIBUTING.md).
2. Open an Issue before beginning a substantial change.
3. Start with a `good first issue`, or propose a focused improvement.

For security concerns, see [SECURITY.md](SECURITY.md).

## License

MAGI is source-available under the [Business Source License 1.1](LICENSE).
Personal use, academic research, education, and evaluation are free. Commercial
production use requires a separate written license until the applicable version
has been publicly available for six months; that version then becomes available
under the MIT License. This is not an OSI-approved open-source license before
its Change Date.

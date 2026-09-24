# MAGI — Modular Agentic Genesis Intelligences

[![License](https://img.shields.io/badge/license-BUSL--1.1-blue.svg)](LICENSE)
[![TypeScript](https://img.shields.io/badge/runtime-TypeScript%20%2B%20Bun-blue)](https://bun.sh)
[![Status](https://img.shields.io/badge/status-experimental-orange)](#project-status)

[中文 README](README_zh.md)

> **MAGI is a multi-agent runtime built toward recursive self-improvement.**
>
> Each MAGI has its own runtime, workspace, memory, tools, and provider
> credentials. ASP starts that process and relays its sessions. The operator
> can inspect the workspace and the boundary around it. Together, MAGI can
> work on the software that runs them: propose changes, evaluate the results,
> and build on improvements over successive cycles.

MAGI is built around a question:

**Can a persistent group of agents improve its own software and organization,
measure the result, and use what it learns to make the next improvement?**

**Genesis** evokes the creation of new agents, capabilities, and ways of
working. **Intelligences** refers to the independent agents involved. Recursive
self-improvement (RSI) is the research direction, not a claim that a complete
autonomous improvement loop exists today.

## Why MAGI?

Most multi-agent systems assemble a temporary team around a workflow: assign a
research task, collect a result, then tear the team down. A MAGI stays. Its
workspace, memory, and skills remain after the task.

| Task-oriented multi-agent orchestration | A MAGI |
| --- | --- |
| Agents are steps in a workflow | A MAGI persists across tasks |
| Collaboration ends with a task | Context, memory, skills, and contacts persist |
| One process commonly hosts many agents | Every MAGI has its own runtime and workspace |
| A controller defines the execution path | The MAGI decides inside its own process. ASP starts it and relays sessions |
| Scale means adding concurrent calls | Scale means adding MAGI |

A workflow engine can still sit beside MAGI. MAGI is the runtime a long-lived
agent runs on. A local, editable source tree gives those agents a place to
change their own software; evaluating and safely adopting such changes is the
next challenge.

## Design philosophy

MAGI is designed for a future in which **intelligence becomes cheaper and more
abundant**, while **coordination, trust, security, and governance remain hard**.

That leads to five principles:

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
- **Test before adopting change.** A proposed change to an agent, a tool, or
  the runtime needs a clear goal, an observable result, and a way to reject or
  revert it. Editing source code alone does not establish improvement.

The long-term goal is for independent agents to collaborate on their own
improvement **within explicit, inspectable constraints**.

## Repository layout

```text
shell/       Electron shell: window, checkout, and bundled Node, npm, and Bun
app/         Operator UI and the local backend that starts ASP
asp/         ASP server (TypeScript, node main.ts): /sessions, WS /connect, ~/.magi/asp.sqlite
magi/        MAGI runtime: BUS, workers, tools, and channels
```

## Toward recursive self-improvement

MAGI should become better because they have existed. The intended loop is to
observe work, propose a change, evaluate it, and retain what proves useful:

- A MAGI learns from the outcomes, failures, and observations of its work.
- Useful procedures become reusable Skills rather than disappearing into one
  conversation.
- MAGI can work toward changing their own code and runtime, then evaluate
  whether those changes improve future behavior.
- The operator can inspect its memory, its tools, its resource boundaries, and
  the authority used to change it.
- Several MAGI can join one operator conversation. Each keeps its own workspace.

> **Implementation status:** a local desktop, one ASP process, and one Bun
> process per MAGI are what runs today. Each MAGI keeps its own workspace
> (memory, skills, tasks, contacts, prompts) and talks to the operator through
> ASP. The local source checkout is editable, but autonomous validation,
> activation, and rollback of agent-proposed code changes are **not yet
> implemented**.

## The MAGI model

| Term | Meaning |
| --- | --- |
| **MAGI** | One autonomous, governable agent. Also the Bun runtime in `magi/`. |
| **EVA** | A naming pattern for handles. ASP assigns `eva-000`, `eva-001`, and so on. The address is `@eva-000.magi`. |

ASP is the local lifecycle boundary. It starts MAGI processes and relays their
sessions. It does not decide what a MAGI should say.

## What exists today

- **Desktop** — an Electron shell clones this repository to `~/.magi/MAGI`,
  installs `asp/` and `magi/`, builds the operator UI, and starts ASP. The
  package carries Node.js 24, npm, and Bun. It does not carry a Python runtime.
- **ASP** — Node 24, `asp/main.ts`, bound to `127.0.0.1:42069`. It stores
  sessions and relay events in `~/.magi/asp/asp.sqlite`. Creating a bot spawns
  that MAGI; creating a group opens a conversation the operator can invite
  MAGI into.
- **One process per MAGI** — Bun runs `magi/magi.ts`. The default workspace is
  `~/.magi/<name>`. An older `~/.magi/ts-magi/<name>` directory is still
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
the preparation stages before opening the WebUI. When no MAGI exists yet, the
backend seeds the first three, **MELCHIOR**, **BALTHASAR**, and **CASPER**.
The app is also the
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
   ├── magi  eva-000     ~/.magi/eva-000
   ├── magi  eva-001     ~/.magi/eva-001
   └── magi  eva-002     ~/.magi/eva-002
```

ASP starts processes and relays session events. It is not the place a MAGI
reasons. Each MAGI keeps its own SQLite workspace. The desktop keeps the
operator's transcript and provider key.

For collaboration between MAGI, **ASP is the central session channel**: it
tracks participants and relays events to the intended agents. **Inside one
MAGI, the BUS is the shared persistence and coordination boundary.** Its
Books and Jobs give Workers and other components one dependency for local
state and work, without requiring them to depend directly on each other.

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
desktop, ASP, and one Bun runtime per MAGI.

The broader vision is for MAGI to improve their own software and organization,
evaluate those changes, and repeat the process. That RSI loop remains a research
goal, distinct from the runtime capabilities described above.

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

# MAGI — Modular Agentic Governed Intelligences

[![License](https://img.shields.io/badge/license-BUSL--1.1-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://python.org)
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
desktop/     Electron bootstrap shell and editable operator WebUI
magi-asp/    Python package magi_asp: /sessions, WS /connect, ~/.magi/asp.sqlite
py-magi/     One MAGI runtime per process; workspace sqlite of its own
ts-magi/     TypeScript BUS playground and its launcher
```

The projects are siblings. Python production code lives at the
`py-magi/` project root (`from bus import Bus`, `from startup.cli import main`).

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

> **Implementation status:** durable memory, Skills, Society/MAGI modeling,
> isolated EVA lifecycle management, restricted control-plane boundaries,
> and **same-MAGIS MAGI↔MAGI collaboration via a persistent actor effect**
> (two terminal modes — `notify` / `request` — backed by shared
> `a2a_request_job_board` / `a2a_notify_job_board` job boards, the
> `message_magi` tool, and a per-turn MAGIS collaboration directory)
> are the foundation available today. Autonomous cross-MAGI learning,
> capability assessment, self-directed organizational restructuring,
> richer policy enforcement, and inter-Society knowledge exchange are
> active design goals; they are **not all implemented yet**.

## The MAGI model

The names are deliberate:

| Term | Meaning |
| --- | --- |
| **MAGI** | The general kind of autonomous, governable agent in this system. |
| **MAGIS** | A **MAGI Society**: an organization of MAGI. Societies form a tree. |
| **MAGIC** | Internal table/API name for an individual MAGI. It is not a separate product concept. |
| **ADAM** | The leading MAGI of a Society. ADAM provides its control plane and coordinates its MAGI. |
| **EVA** | A working MAGI role. A Society can create, configure, start, stop, and retire multiple EVAs. |

```text
Operator
   │ WebUI
   ▼
MAGIS: Engineering
   │
   ├── ADAM / MAGI                      control plane and coordinator
   │      └── durable Society memory, policy, and relationships
   │
   ├── EVA / MAGI                       independent runtime + workspace
   ├── EVA / MAGI                       independent runtime + workspace
   └── child MAGIS: Research            its own ADAM and MAGI

MAGIS-shared database (intra-Society MAGI↔MAGI):
   a2a_request_job_board   ─┐
   a2a_notify_job_board    ─┴─► AgentWorker (target magi_id) persistent actor effect
                              message_magi {magi_id, mode, text, deadline_seconds}
```

ADAM is a coordinator, not an unrestricted host administrator. The ASP service
owns lifecycle operations and starts only scoped local MAGI processes.

## What exists today

- **Independent runtimes** — ADAM and every EVA run as separate local
  processes with their own workspace.
- **Society administration** — the WebUI manages MAGIS trees and MAGI,
  including ADAM assignment and EVA provider configuration.
- **EVA lifecycle control** — the ASP service starts a local MAGI process when
  the operator creates a bot.
- **Persistent operational memory** — conversation history, contact knowledge,
  task state, and searchable stored memory survive across conversations.
- **Channels and tools** — WebUI is available now; Telegram, MCP servers,
  Skills, scheduled tasks, and built-in tools extend what a MAGI can do.
- **Provider independence** — MAGI hold their own provider configuration
  and API credentials rather than sharing one global model account.
- **Same-MAGIS A2A collaboration** — MAGI of the same Society collaborate
  through a **persistent actor effect**: messages land on two job boards
  in the MAGIS-shared database (`a2a_request_job_board` for one-shot
  request / one response, `a2a_notify_job_board` for durable one-way
  notifications) and are claimed directly by the target MAGI's
  `AgentWorker` — never via HTTP, webhooks, or external signature
  protocols. The tool contract collapses to
  `message_magi({magi_id, mode ∈ {notify, request}, text, deadline_seconds})`.
  Each MAGI's `responsibility` (scope statement) and the rest of its
  MAGIS's directory are rendered into every system prompt, so the model
  sees boundaries and specialisations before it picks a collaborator.

## Quick start

MAGI currently runs as a local ASP service plus local MAGI processes. There is
no root `deploy/` tree.

| Situation | Where | What you run |
| --- | --- | --- |
| Desktop client | [`desktop/`](desktop/) | Open the Electron app. It starts local ASP; ASP starts MAGI. |

**Desktop:** on first launch, the app clones the complete repository into
`~/.magi/MAGI`, prepares local dependencies, builds the WebUI, and starts ASP
on [http://127.0.0.1:42069](http://127.0.0.1:42069). A startup page shows
the preparation stages before opening the WebUI. The app is also the
machine-local layer: connecting the checkout to the operator's GitHub account
(fork plus `origin`) happens in the WebUI after startup, not while booting.
Creating a bot is `POST /conversations { "kind": "bot" }` — ASP assigns
`eva-000` and starts that MAGI.

**One MAGI:** `python -m magi <handle> <base> <token>` (or the `magi` console script). py-magi is a single MAGI process.


## From the first MAGIS to a growing organization

1. **Initialize Genesis.** `magi init` provisions the root MAGI
   Society, **Genesis**, then creates the first MAGI, **`eva-000`**,
   as Genesis's ADAM.
2. **Secure the default administrator.** Configure an IM verification channel
   in Settings; normal local use remains available while the security reminder
   is open.
3. **Shape the organization.** In WebUI, create child MAGIS entries and
   assign their ADAM MAGI.
4. **Add capability.** Configure an EVA's provider and credentials, then ask
   its ADAM to start or stop that MAGI through the orchestrator.
5. **Accumulate intelligence.** Conversations, task outcomes, contacts,
   memory, and reusable Skills remain part of the Society instead of being
   discarded when a single request ends.
6. **Govern autonomy.** As the Society grows, keep lifecycle authority,
   credentials, workspaces, and operator-visible boundaries explicit rather
   than collapsing every MAGI into one unrestricted process.

## Architecture

```text
                        ┌─────────────────────────────┐
                        │          Operator           │
                        │            WebUI            │
                        └──────────────┬──────────────┘
                                       │
                        ┌──────────────▼──────────────┐
                        │          ADAM / MAGI        │
                        │    Society control plane    │
                        └──────────────┬──────────────┘
                                       │ lifecycle request
                        ┌──────────────▼──────────────┐
                        │          MAGI ASP           │
                        │    local process launcher   │
                        └───────┬──────────────┬───────┘
                                │              │
                     ┌──────────▼───┐  ┌──────▼──────────┐
                     │ EVA / MAGI   │  │ EVA / MAGI      │
                     │ local process│  │ local process   │
                     └──────────────┘  └─────────────────┘
```

ASP is the **lifecycle authority, not the Society's reasoning brain**. It
starts local processes while MAGI retain their own runtime, state, tools, and
role in the Society. Each MAGI keeps its own local SQLite workspace.

### Desktop UI and ASP

The Electron app starts a local ASP service. ASP owns HTTP, WebSocket
`/connect`, and MAGI process creation; the desktop does not start MAGI
directly. The WebUI is built and loaded from the local checkout.

### Local source and the path to RSI

The packaged app supplies an Electron bootstrap shell and private Git,
Python, and Node.js tools. On first launch it clones the complete repository
to `~/.magi/MAGI`. Later launches keep that Git working tree and use its
`py-magi/`, `magi-asp/`, and `desktop/app/` sources. Local changes are not
overwritten or pulled automatically. A user or coding agent can edit the
checkout, rebuild the interface, and merge future upstream changes using Git.
Once the operator connects GitHub from the interface, `origin` is their fork and
`upstream` stays the repository the app cloned, so pushes land in their own
account.
When `desktop/app/dist/index.html` changes, the app asks before reloading.

Only the bootstrap stays in the installed package: `desktop/shell/` clones,
prepares, starts ASP and then loads the app from the checkout — both its
interface (`desktop/app/src/`) and its local backend (`desktop/app/main/`),
which the shell reaches through one generic bridge. Editing either one takes
effect without a new package; editing `desktop/shell/` still does not change the
running shell. Code changes in MAGI or ASP also need the affected process
to restart. The editable checkout is the foundation for RSI, **not** an
autonomous self-update system yet: MAGI does not currently validate, activate,
restart, or roll back its own code revisions as one managed operation.
See [desktop details](desktop/README.md).

For the implementation-level view, see:

- [Architecture](docs/ARCHITECTURE.md)
- [Business flows](docs/business-flows.md)
- [Terms and canonical ID names](docs/terms.md)
- [magi-asp](magi-asp/README.md)
- [Roadmap](docs/ROADMAP.md)

## Project status

MAGI is experimental and under active construction. The present codebase is a
working foundation for Society modeling, onboarding, isolated node deployment,
persistent runtime state, and EVA lifecycle control.

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

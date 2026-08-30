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

That leads to three principles:

- **Do not hard-code around temporary model limitations.** Token cost, context
  size, and reasoning quality will change quickly; the architecture should not
  depend on them staying scarce.
- **Prefer protocol-mediated coordination over rigid workflow control.** As
  agents become more capable, infrastructure should increasingly define how
  agents discover, communicate, and delegate — not prescribe every reasoning step.
- **Keep governance mandatory.** Identity, permissions, isolation, observability,
  resource boundaries, and accountability become more important as agents gain
  more autonomy.

The long-term goal is to build the infrastructure in which autonomous
intelligences can collaborate freely **within explicit, inspectable constraints**.

## Repository layout

```text
py-magi/   Python backend and launcher (import package: magi)
ts-magi/   TypeScript BUS playground and its launcher
webapp/    Browser operator UI and its SQLite-backed local application data
desktop/   Electron shell, consuming the webapp package
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

ADAM is a coordinator, not an unrestricted host administrator. It is not
granted the host Docker socket or broad Kubernetes credentials. Instead, it
requests lifecycle changes through a restricted, authenticated orchestrator.
The control plane creates only the scoped private MAGI workspace and runtime,
plus the shared-database and public workspace resources for a MAGIS when needed.

## What exists today

- **Independent runtimes** — ADAM and every EVA run as separate Kubernetes
  Deployments with their own persistent workspace.
- **Society administration** — the WebUI manages MAGIS trees and MAGI,
  including ADAM assignment and EVA provider configuration.
- **EVA lifecycle control** — an ADAM can request EVA start, stop, and delete
  operations through the in-cluster orchestrator.
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

Pick the deployment that matches your situation. Both paths are supported and
live under `deploy/`. All startup code paths converge on
`magi.startup`:

| Situation | Path | Entry point |
| --- | --- | --- |
| I want a single-machine MAGI on my laptop/desktop | [deploy/cli/](deploy/cli/) | `./deploy/cli/install.sh` (installs, initializes, and starts MAGI) |
| I have an existing cluster and want to deploy to it | [deploy/k8s/](deploy/k8s/) | `./deploy/k8s/bootstrap-k8s.sh` |

The **single-machine path** is the fastest way to take MAGI for a
spin. It runs directly on the host (no Docker, no k8s) and stores
state under `~/.magi/` (Linux) or `~/Documents/.magi/` (macOS,
Windows). Run `./deploy/cli/install.sh` once: it installs MAGI, provisions
the first MAGI (`eva-000`) and the root MAGI Society **Genesis**, then starts
the Runtime and WebUI. Open [http://127.0.0.1:42069](http://127.0.0.1:42069),
select the running MAGI, then choose the default `admin` account. This local
bootstrap access is intentionally usable without a password; enable IM
two-factor verification from Settings before adding administrators or assigned
users. Afterwards, `magi start` safely preserves the existing Society and
recovers services that are not running. Each new MAGI is a separate
process: `magi node create --name eva-001`, then `magi node run --name eva-001`.

For an existing cluster or a production-style deployment, use the
k8s production path:

```bash
MAGI_IMAGE=registry.example.com/your-team/magi:0.1.0 \
  ./deploy/k8s/bootstrap-k8s.sh
```

See the deployment guides for image, storage, networking, Secrets,
and environment-specific configuration.

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
                                       │ authenticated lifecycle request
                        ┌──────────────▼──────────────┐
                        │       MAGI Orchestrator     │
                        │   restricted Kubernetes API │
                        └───────┬──────────────┬───────┘
                                │              │
                     ┌──────────▼───┐  ┌──────▼──────────┐
                     │ EVA / MAGI   │  │ EVA / MAGI      │
                     │ Deployment   │  │ Deployment      │
                     │ PVC + Secret │  │ PVC + Secret    │
                     └──────────────┘  └─────────────────┘
```

The orchestrator is a **lifecycle authority, not the Society's reasoning
brain**. Its job is to enforce a narrow execution boundary around operations
that require infrastructure privileges, while MAGI retain their own runtime,
state, tools, and role in the Society.

Kubernetes is the current deployment target. It gives each MAGI a concrete
execution boundary and lets the orchestrator manage isolated runtime resources
without making ADAM a cluster administrator. Each MAGI keeps a private,
single-replica SQLite workspace under
`/MAGI_Citizens/<MAGI_NAME>/memories/magi.db` — the path resolver
detects `KUBERNETES_SERVICE_HOST` and defaults `HOST_WORKSPACE_DIR`
to `/`, the PVC mounts the container root, and `MAGI_Citizens/<name>`
is derived from `MAGI_NAME`. Each MAGIS has its own database and public
workspace PVC for organization facts and shared files: local deployments use
an isolated SQLite file under `MAGI_Societies/<MAGIS_NAME>/`, while Kubernetes
provisions one database per MAGIS in a shared PostgreSQL service. The startup
inputs (`HOST_WORKSPACE_DIR`, `MAGI_NAME`, `MAGIS_NAME`,
`MAGIS_DATABASE_URL`, `MAGI_ID`) are
the only contract Runtime sees; workspace paths are derived, never
configured. See [the storage boundary](docs/ARCHITECTURE.md#storage-ownership)
for the exact split and contract.

### One WebUI, one image

MAGI uses one container image with two selectable service roles. The default
`magi` command runs one MAGI and exposes only an internal Runtime API. The
singleton `magi webui` command serves the React application, authentication,
organization control plane, and a protected proxy to the selected MAGI. A
browser therefore always visits one WebUI Service; it never connects directly
to an individual MAGI Pod.

The landing page first selects a running MAGI, then offers only that MAGI's
direct MAGIS administrators and assigned user. The proxy signs each internal
request with an HMAC derived from the per-MAGIS `control_secrets` row,
binding it to the selected MAGI and the authenticated identity. Each runtime
rejects a request addressed to a different MAGI. Selecting another MAGI
requires a new login; it is not an in-dashboard target switch. A MAGI's own
Bot sends login codes when configured; otherwise its direct MAGIS ADAM Bot
provides the one-time bootstrap fallback.

For the implementation-level view, see:

- [Architecture](docs/ARCHITECTURE.md)
- [Business flows](docs/business-flows.md)
- [Terms and canonical ID names](docs/terms.md)
- [Deployment overview](deploy/README.md)
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

# MAGI — Modular Agentic Group Intelligence

[![License](https://img.shields.io/badge/license-BUSL--1.1-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://python.org)
[![Status](https://img.shields.io/badge/status-experimental-orange)](#project-status)

[中文 README](README_zh.md)

> **MAGI is a runtime for persistent, modular agent societies.**
>
> A MAGIS is not a one-off group chat or a task pipeline. It is a MAGI Society:
> an
> organization of independent MAGI: each has its own runtime,
> workspace, memory, tools, provider credentials, and role in the Society.
> They coordinate through Adam, execute through EVEs, retain what they learn,
> and grow into a durable collective intelligence.

MAGI is built for the question beyond “how do I delegate this task?”:

**How do we give a group of AI agents an identity, continuity, organization,
and the ability to improve together over time?**

## Why MAGI?

Most multi-agent systems assemble a temporary team around a workflow: assign a
research task, collect a result, then tear the team down. MAGI treats the
organization itself as the primary unit.

| Task-oriented multi-agent orchestration | MAGI Society runtime |
| --- | --- |
| Agents are steps in a workflow | MAGI are persistent members of an organization |
| Collaboration ends with a task | Context, memory, skills, and relationships persist |
| One process commonly hosts many agents | Every MAGI has an independent containerized runtime and workspace |
| A manager delegates predefined work | Adam coordinates a Society; EVEs are independently managed, started, and stopped |
| Scale means adding concurrent calls | Scale means adding capable MAGI and connected Societies |

MAGI does not replace workflow engines. It provides the substrate on which a
long-lived agent organization can operate, learn, and evolve.

## Toward collective intelligence

The end state is not a static hierarchy that repeatedly delegates prompts. A
MAGIS should become better because it has existed:

- MAGI learn from the outcomes, failures, and observations of their work.
- Useful procedures become reusable Skills rather than disappearing into an
  individual conversation.
- Adam can recognize capability gaps, organize specialized EVEs, and reshape
  the Society as its work changes.
- Societies can share knowledge and collaborate without reducing every member
  to a stateless API call.
- Operators remain able to inspect the organization, its memory, its tools,
  and the authority used to change it.

> **Implementation status:** durable memory, Skills, Society/MAGI modeling,
> and isolated EVE lifecycle management are the foundation available today.
> Autonomous cross-MAGI learning, capability assessment, self-directed
> organizational restructuring, and inter-Society knowledge exchange are
> active design goals; they are **not implemented yet**.

## The MAGI model

The names are deliberate:

| Term | Meaning |
| --- | --- |
| **MAGI** | The general kind of autonomous agent in this system. |
| **MAGIS** | A **MAGI Society**: an organization of MAGI. Societies form a tree. |
| **MAGIC** | Internal table/API name for an individual MAGI. It is not a separate product concept. |
| **Adam** | The leading MAGI of a Society. Adam provides its control plane and coordinates its MAGI. |
| **EVE** | A working MAGI role. A Society can create, configure, start, stop, and retire multiple EVEs. |

```text
Operator
   │ WebUI
   ▼
MAGIS: Engineering
   │
   ├── Adam / MAGI                      control plane and coordinator
   │      └── durable Society memory, policy, and relationships
   │
   ├── EVE / MAGI                       independent runtime + workspace
   ├── EVE / MAGI                       independent runtime + workspace
   └── child MAGIS: Research             its own Adam and MAGI
```

An Adam is not granted the host Docker socket or broad Kubernetes credentials.
It requests lifecycle changes through a restricted, authenticated orchestrator.
The control plane creates only the scoped private MAGI workspace and runtime,
plus the PostgreSQL and public workspace resources for a MAGIS when needed.

## What exists today

- **Independent runtimes** — Adam and every EVE run as separate Kubernetes
  Deployments with their own persistent workspace.
- **Society administration** — the WebUI manages MAGIS trees and MAGI,
  including Adam assignment and EVE provider configuration.
- **EVE lifecycle control** — an Adam can request EVE start, stop, and delete
  operations through the in-cluster orchestrator.
- **Persistent operational memory** — conversation history, contact knowledge,
  task state, and searchable stored memory survive across sessions.
- **Channels and tools** — WebUI is available now; Telegram, MCP servers,
  Skills, scheduled tasks, and built-in tools extend what a MAGI can do.
- **Provider independence** — MAGI hold their own provider configuration
  and API credentials rather than sharing one global model account.

## Quick start: a local dev cluster

The fastest path starts a local `kind` cluster and the first development MAGI
node. Docker is the only host prerequisite. The script downloads its pinned
`kind` and `kubectl` tools locally when needed, builds the images, creates
the cluster, and deploys the dev node with backend reload and Vite HMR.

```bash
git clone https://github.com/realTaki/MAGI.git
cd MAGI
./deploy/bootstrap-local.sh
```

Open [http://127.0.0.1:42069](http://127.0.0.1:42069) and complete onboarding.
During system initialization, MAGI automatically creates the root MAGI Society,
**Genesis**. It then creates **EVA-00 PROTO TYPE**, the first MAGI,
as Genesis's Adam.

The local development deployment mounts:

```text
host repository      → /app/magi        source hot reload
workspace/MAGIC/eva-00 → /workspace     dev MAGI's private workspace
workspace/MAGIS/Genesis → /magis         Genesis public workspace
```

For an existing cluster or a production-style deployment, use:

```bash
MAGI_IMAGE=registry.example.com/your-team/magi:0.1.0 \
  ./deploy/bootstrap-k8s.sh
```

See [the Kubernetes deployment guide](deploy/k8s/README.md) for image,
storage, networking, Secrets, and environment-specific configuration.

## From the first MAGIS to a growing organization

1. **Initialize Genesis.** MAGI creates the root MAGI Society, Genesis, then
   creates **EVA-00 PROTO TYPE**—the first MAGI—as Genesis's Adam.
2. **Onboard an operator.** Configure administrator access and the channels
   your Society should use.
3. **Shape the organization.** In WebUI, create child MAGIS entries and
   assign their Adam MAGI.
4. **Add capability.** Configure an EVE's provider and credentials, then ask
   its Adam to start or stop that MAGI through the orchestrator.
5. **Accumulate intelligence.** Conversations, task outcomes, contacts,
   memory, and reusable Skills remain part of the Society instead of being
   discarded when a single request ends.

## Architecture

```text
                        ┌─────────────────────────────┐
                        │          Operator           │
                        │            WebUI            │
                        └──────────────┬──────────────┘
                                       │
                        ┌──────────────▼──────────────┐
                        │          Adam / MAGI               │
                        │   Society control plane      │
                        └──────────────┬──────────────┘
                                       │ authenticated lifecycle request
                        ┌──────────────▼──────────────┐
                        │       MAGI Orchestrator      │
                        │   restricted Kubernetes API  │
                        └───────┬──────────────┬───────┘
                                │              │
                     ┌──────────▼───┐  ┌──────▼──────────┐
                     │ EVE / MAGI          │  │ EVE / MAGI              │
                     │ Deployment   │  │ Deployment      │
                     │ PVC + Secret │  │ PVC + Secret    │
                     └──────────────┘  └─────────────────┘
```

Kubernetes is the current deployment target. It gives each MAGI a concrete
execution boundary and lets the orchestrator manage isolated runtime resources
without making Adam a cluster administrator. Each MAGI keeps a private,
single-replica SQLite workspace; each MAGIS has its own PostgreSQL database and
public workspace PVC for organization facts and shared files. See
[the storage boundary](docs/magi-magis-storage.md) for the exact split.

### One WebUI, one image

MAGI uses one container image with two selectable service roles. The default
`magi` command runs one MAGI and exposes only an internal Runtime API. The
singleton `magi webui` command serves the React application, authentication,
organization control plane, and a protected proxy to the selected MAGI. A
browser therefore always visits one WebUI Service; it never connects directly
to an individual MAGI Pod.

The proxy derives the target from the MAGI registry and signs each internal
request with `MAGI_CONTROL_SECRET`, binding it to the selected MAGI and the
authenticated operator. Each runtime rejects a request addressed to a different
MAGI. Selecting another MAGI changes the target scope of private views such as
chat, memory, SOUL, skills, tasks, and settings.

For the implementation-level view, see:

- [Architecture](docs/ARCHITECTURE.md)
- [Unified WebUI and Runtime API](docs/unified-webui.md)
- [Business flows](docs/business-flows.md)
- [Database and migration notes](docs/database-migrations.md)
- [Kubernetes deployment](deploy/k8s/README.md)
- [Roadmap](docs/ROADMAP.md)

## Project status

MAGI is experimental and under active construction. The present codebase is a
working foundation for Society modeling, onboarding, isolated node deployment,
and EVE lifecycle control. The collective-intelligence mechanisms described
above are intentionally part of the public project vision; their implementation
status is stated explicitly so the README remains ambitious without confusing
roadmap with shipped behavior.

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

# MAGI — Modular Agentic Group Intelligence

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://python.org)
[![Status](https://img.shields.io/badge/status-experimental-orange)](#)

**An Agentic Society.**
MAGI is infrastructure for groups of autonomous agents that self-organize into teams,
coordinate among themselves, and act as a collective. A MAGI is not a chatbot — it is a
member of a society. When you deploy one, you are not installing software; you are adding
a citizen to a group intelligence.

Not a single-agent tool. Not a multi-turn chat wrapper. This is **group intelligence as a runtime**.

---

True agentic societyAgents form organizations (MAGICs) with leaders and members, collaborate on complex tasks, and persist shared state across sessions. Each agent has a position in its society — who it leads, who it follows, what it knows. Self-organizing teams leaderless task distribution, delegated subtasks, coordinated execution. A group of agents working together is fundamentally more capable than any single one. Every agent is first-classEach MAGI runs in its own container, has its own memory, its own tools, its own LLM credentials — and its own identity. Not threads. Not sessions. Citizens. Persistent collective memoryThe society remembers. Conversation history, long-term facts, contact knowledge, task results — all searchable, all durable. The group gets smarter over time. Runs anywhere, scales with needOne container per agent. SQLite for a laptop; Postgres for a fleet. Docker Compose for one team; Kubernetes for many. The runtime is the same at every scale. BYO intelligence providerAnthropic, Minimax, OpenAI, or your own endpoint. Every agent configures its own LLM — billed to its own budget. No vendor lock-in. No house account. Open by designMIT licensed. Extensible tool system. MCP-compatible. Built-in skills framework. Every part of the society can be customized.

---

## Quick Start

```bash
git clone https://github.com/MAGI/MAGI.git
cd MAGI
cp deploy/.env.example deploy/.env
docker compose -f deploy/docker-compose.yml up --build
# → http://localhost:42069
```

Walk through the first-time setup wizard — save a Telegram bot token, verify administrator identities, and your first MAGI society is live.

📖 [Documentation →](docs/) &nbsp;|&nbsp; 🗺 [Roadmap →](docs/ROADMAP.md)

---

## The Society Model

| Entity | Role in the society |
|---|---|
| **MAGI** | The society itself — a collective of agents coordinating, learning, and acting together |
| **MAGIC** | An organization (council). One leader, many members. Councils can contain sub-councils |
| **Adam** | A leader agent. Manages its council, dispatches work, holds shared state |
| **EVE** | A member agent. Executes tasks, collaborates with peers, reports to its Adam |
| **Contact** | A participant known to the society. Operates the council or receives its output |

Agents form a **tree of councils** — each MAGIC has one Adam at its root, coordinating a
set of EVEs. Councils can delegate to child councils. The society scales by adding agents,
not by complicating the architecture.

---

## Capabilities

**Coordination & Delegation**
Agents distribute tasks among themselves. An Adam breaks down complex work, assigns
subtasks to its EVEs, and synthesizes results. EVEs can spawn sub-agents for parallel
workstreams. The society handles the routing — you describe the outcome.

**Persistent Memory Across the Collective**
Three-layer memory architecture: conversation history (what was said), contact knowledge
(who is in the society and what we know about them), and self-memory (what the society
has learned). Full-text search across all layers. The group remembers, so you don't have
to repeat yourself.

**Proactive Intelligence**
Agents don't just respond — they anticipate. Scheduled tasks, periodic reports, automated
follow-ups. An Adam can be configured to check in on its EVEs, compile status summaries,
and surface issues before they become problems. The society runs unattended.

**Multi-Channel Presence**
Every agent is reachable. WebUI for the operator console. Telegram for real-time
interaction. The channel dispatcher routes messages to the right agent regardless
of which surface the message arrived on. A single agent can be present on multiple
channels simultaneously.

**Tool Ecosystem**
20+ built-in tools cover file operations, shell execution, web search, memory
management, scheduling, and more. MCP-compatible for external tool servers.
Agents can create and refine skills — procedural knowledge that improves with use.
The society's tool repertoire grows with it.

**Identity & Access**
Every agent has its own identity, credentials, and permissions. Every action is
attributed — audit trails track who did what, when, through which channel. The
society's governance is transparent by default.

---

## Architecture at a Glance

```
                    ┌─────────────────────────────┐
                    │         Operator (WebUI)     │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │       Adam (Leader Agent)    │
                    │   Council: "Engineering"     │
                    └──┬───────────┬───────────┬──┘
                       │           │           │
              ┌────────▼──┐  ┌─────▼─────┐  ┌──▼────────┐
              │  EVE α    │  │  EVE β    │  │  EVE γ    │
              │  Telegram │  │  Telegram  │  │  Telegram  │
              └───────────┘  └───────────┘  └───────────┘

        Each agent = one container. Each council = one leader + many members.
        Councils form trees. The society is the whole graph.
```

Detailed architecture in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Contributing

MAGI is written and maintained by AI in collaboration with humans. Contributions welcome.

1. Read [CONTRIBUTING.md](CONTRIBUTING.md)
2. Open an Issue before writing code
3. Pick a `good first issue` or propose your own

Security → [SECURITY.md](SECURITY.md).

---

## License

MIT — see [LICENSE](LICENSE).

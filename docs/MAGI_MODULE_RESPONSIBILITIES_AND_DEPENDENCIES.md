# MAGI Module Responsibilities and Dependencies

## Core rule

BUS (`magi.bus`) is MAGI's only shared runtime and persistence boundary. Domain
modules interact through its public facade, Books, Job Boards, DTOs, and
errors. They do not import its database implementation.

| Module | Owns | Depends on |
| --- | --- | --- |
| `magi.bus` | `Bus`, SQLite/MAGIS factories, Books, Job Boards, durable state | SQLAlchemy, drivers, filesystem |
| `magi.startup` | path resolution and process composition | `magi.bus`, worker entry points |
| `magi.agent` | agent transition and prompt/context orchestration | `magi.bus`, prompts, providers through jobs |
| `magi.tools` | tool contracts, registry, and ToolWorker | `magi.bus` |
| `magi.providers` | provider adapters and LLM worker | `magi.bus` |
| `magi.mcp` | MCP connection lifecycle and adapter registration | `magi.bus`, `magi.tools` |
| `magi.channels` | HTTP, WebUI, Telegram, task, and A2A adapters | `magi.bus` |
| `magi.proactive` | scheduled/proactive policy and worker | `magi.bus` |

## Boundary rules

- `magi.bus.db` is private to BUS. ORM rows, sessions, engines, and table
  registration never leave BUS.
- Books return DTOs or JSON-safe values; Job Boards carry durable job/result
  DTOs.
- Workers claim jobs and perform LLM, tool, network, or channel I/O outside
  database transactions.
- BUS does not import domain worker implementations. The startup composition
  root is the only place that joins BUS and workers.
- Plugins and adapters receive narrow contracts, not a persistence handle.

## Worker flow

```text
ingress -> Book write + Job Board publish -> worker claim -> external effect
        -> Job Board result / Book update -> client replay or delivery
```

The committed BUS result is authoritative. Stream notifications accelerate
interactive clients but do not replace durable state.

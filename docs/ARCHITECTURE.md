# MAGI Architecture

The current MAGI runtime is organized around one durable boundary: **BUS**
(`magi.bus`). For detailed contracts and dependency rules, see
[MAGI BUS-Centric Architecture](MAGI_BUS_CENTRIC_ARCHITECTURE.md).

## Runtime shape

```text
channel/API -> BUS Job Board -> worker -> external effect
                    |              |
                    +-- Books <----+-- durable result
```

The composition root creates one `Bus` instance with `bootstrap_bus(...)` and
injects it into workers. BUS owns local SQLite, optional MAGIS database access,
file-backed prompt/skill Books, durable Job Boards, and the DTO boundary.

`magi.agent`, `magi.channels`, `magi.tools`, `magi.mcp`, `magi.providers`, and
`magi.proactive` depend on BUS; BUS does not depend on their implementations.
External effects are worker responsibilities, and persistent state is committed
through Books or Job Boards before clients observe terminal success.

## Important paths

| Path | Responsibility |
| --- | --- |
| `magi/bus/` | BUS facade, persistence implementation, Books, Job Boards, streams |
| `magi/startup/runtime.py` | composition root and worker lifecycle |
| `magi/agent/worker.py` | durable agent-job consumer |
| `magi/tools/worker.py` | durable tool-effect consumer |
| `magi/providers/worker.py` | durable LLM-job consumer |
| `magi/channels/` | external ingress/egress adapters |

There is no alternate BUS implementation or compatibility import path.

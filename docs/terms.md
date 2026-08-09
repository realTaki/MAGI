# MAGI Terms

| Term | Meaning |
| --- | --- |
| **MAGI** | Modular Agentic Group Intelligence, the product and runtime family. |
| **MAGIS** | A MAGI Society: an organization containing MAGI runtimes. |
| **MAGIC** | One concrete MAGI runtime process and its private state. |
| **ADAM** | A manager-archetype MAGIC that owns the control-plane experience. |
| **EVA** | A worker-archetype MAGIC that serves an assigned employee or workload. |
| **BUS** | The sole durable application boundary, implemented by `magi.bus`. |
| **Bus** | The process-local BUS facade created by `bootstrap_bus(...)`. |
| **Book** | A BUS API for durable CRUD/query operations that returns DTOs or JSON-safe values. |
| **Job Board** | A BUS API for durable `publish -> claim -> submit_result` workflows. |
| **ChatJob** | Durable agent input from a channel, task, or A2A ingress. |
| **DeliveryJob** | Durable outbound message for a channel worker. |
| **MAGI private SQLite** | Per-runtime state database, normally `<workspace>/memories/magi.db`. |
| **MAGIS database** | Organization-scoped database reached through the configured MAGIS URL. |
| **A2A** | Internal agent-to-agent transport; it is not an authorization system. |

The Python package `magi.bus` owns Books, Job Boards, database factories, and
their ORM implementation. Domain code uses its public contracts and does not
open sessions or expose ORM rows.

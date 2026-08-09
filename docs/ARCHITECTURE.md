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

## Channels — ingress/egress adapters

Channels wrap the single BUS implementation. They receive an injected `Bus`;
all durable state changes use Books and Job Boards. The shared template lives
in `magi/channels/worker_base.py::ChannelWorker`; each concrete channel reduces
to a `_run` coroutine plus a `_deliver_<channel>` function passed into the
base class's `_claim_delivery_loop` template method.

| Component | Ingress | Egress |
| --- | --- | --- |
| Telegram worker | validates input, persists message, publishes `ChatJob` | claims `DeliveryJob(channel="tg")` and calls Telegram |
| Task worker | claims `RunTaskJob`, publishes `ChatJob` | none |
| WebUI route | validates request, persists message, publishes `ChatJob`, returns `202` + `run_id` | none |
| WebUI worker | none | claims web UI delivery and persists assistant message |
| A2A route/worker | publishes/completes A2A jobs | claims `SendA2AJob` and performs peer HTTP |

All ingress sources use a stable event id for idempotency. `ChatJob.payload`
contains the reply channel, user/session identifiers, and source context.
`DeliveryJob` is the only normal reply transport; task is an input source, not a
delivery channel. A2A uses its dedicated `a2a_job_board`.

### ChannelWorker template

`ChannelWorker` (in `magi/channels/worker_base.py`) provides:

- Constructor injection of `Bus`.
- `start()` / `stop()` lifecycle (idempotent, task-based; restart-safe).
- `_claim_delivery_loop(deliver_fn, channel_label)` — backpressure → claim →
  release-if-mismatched → deliver → `submit_result` template.
- `health()` — `{name, running, last_poll_at, last_success_at, last_error,
  queue_depth}` snapshot for the `/health/channels` endpoint.

Each outbound worker reduces to a `_deliver_<channel>` function that talks to
the external effect directly (TG → raw HTTP `send_text_raw`; WebUI →
`messages_book.append`; A2A → peer HTTP).

### Channel worker registry

`magi/channels/__init__.py` exposes `registered_channel_workers()` returning
the live `{name: worker}` map, plus `start_channel_workers(bus=...)` /
`stop_channel_workers(workers)` to drive them. `known_channels` is a frozen
set; unknown names log a warning rather than raising. The FastAPI lifespan
in `magi/startup/runtime.py` calls this with the same `Bus` instance created
at composition-root time.

### Task firing — `runTaskJob` board

Inter-worker and tool-side task triggers go through
`bus.run_task_job_board.publish(RunTaskJob(task_id=..., fired_by=...))`. The
`fired_by` field is a closed set (`cron_tick` | `run_at_consume` |
`api_manual_run` | `schedule_task_tool`). TaskWorker drains this board first
in each poll, then falls through to cron / `run_at` ticking. This is the only
legitimate path for ad-hoc task fire; direct `TaskChannel.dispatch` /
`bus.task.schedule_now` style APIs no longer exist.

### Task state persistence

The old `_last_fire` in-memory dict is gone. State is now durable:

- `tasks.last_run_at` / `tasks.last_status` / `tasks.last_error` /
  `tasks.consecutive_failures` are written by `TaskBook.record_run_start` /
  `record_run_end` / `mark_run_at_consumed`.
- `task_runs` rows track per-execution lifecycle (`running` →
  `completed` / `failed`).
- `task_runs.reap_stale(older_than_seconds=300)` flips rows stuck in
  `running` longer than the timeout to `failed` with
  `error="abandoned by previous worker"`; called once at TaskWorker startup
  to recover from a crashed prior instance.
- One-shot `run_at` tasks flip `enabled=0` after a successful fire and can
  never re-fire.

Cron parsing uses `croniter` — the `apscheduler` dependency is gone from this
module.

### Common job lifecycle

`publish → claim → submit_result`. Channel I/O happens after claim and outside
BUS transactions. Workers must release jobs that belong to another channel
(`release` if `job.channel != channel_label`) and tolerate retry after a lease
expires (`BaseJobBoard._claim` re-leases abandoned jobs up to `MAX_ATTEMPTS=3`
before marking them exhausted; channel workers do not retry themselves).

### Backpressure

`_claim_delivery_loop` checks `delivery_job_board.pending_count(channel=...)`
before claiming; if depth exceeds the threshold from
`settings_book.get("channels.delivery.max_queue_depth")` (default `1000`),
it logs a once-per-minute-per-channel warning and sleeps 5× the poll cadence
before retrying. The threshold is a settings key, not a constant.

### Observability

`GET /health/channels` returns `{channels: [{name, running, last_poll_at,
last_success_at, last_error, queue_depth}, ...]}`. Mounted from
`magi/channels/api/health.py` in `magi/channels/api/app.py`.

There is no channel-owned database, fallback runtime, dual-write path, or
second queue. The composition root creates BUS once and starts the required
workers with that same instance.
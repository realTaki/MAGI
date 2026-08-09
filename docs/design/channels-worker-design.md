# Channels and BUS Workers

Channels are ingress/egress adapters around the single BUS implementation.
They receive an injected `Bus`; all durable state changes use Books and Job
Boards.

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

The common job lifecycle is `publish -> claim -> submit_result`. Channel I/O
happens after claim and outside BUS transactions. Workers must release jobs
that belong to another channel and tolerate retry after a lease expires.

There is no channel-owned database, fallback runtime, dual-write path, or
second queue. The composition root creates BUS once and starts the required
workers with that same instance.

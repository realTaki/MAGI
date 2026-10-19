# EVA launcher and agent role

`eva.mjs` is the executable launcher. It starts one
[`packages/node/dist/child.js`](../../packages/node/child.ts) process for
`--role shell`, `--role asp` or `--role agent`. The launcher has no BUS;
the child owns the BUS, manager and role workers. `eva.ts` exports the `Magi`
class and is not the executable composition root.

Use the MAGI-owned runtime and build from the repository root. Runnable commands,
ASP credentials and workspace paths are in [Running MAGI](../../docs/running.md).
The desktop starts its explicit default local agent through an independent
launcher; arbitrary registered agents are not automatically spawned.

Each node has its own provider configuration, prompts, skills, memory and jobs.
An ASP-connected agent uses that node's registered token and public-key identity;
the child includes a CLI handler, but the non-host launcher currently ignores its
stdin (see the running guide). Telegram and MCP workers run when their
workspace configuration enables them. Provider fields are described in the
[catalog](../../docs/provider-catalog.md).

Workers exchange `MessageDeliveryJob`, `CallLLMJob`, `RunToolJob` and related
contracts through BUS. Agent turns are serial per chat; provider and tool work
poll independently. See [Architecture](../../docs/architecture.md) for the worker
boundary, durable delivery and self-replace flow.

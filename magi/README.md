# magi

TypeScript MAGI uses `magi.ts` as its composition root. Every Worker depends
on BUS for shared state, Job exchange, and the tool catalog.

| Module | Responsibility |
| --- | --- |
| `bus/` | Books, durable Jobs, and shared tool catalog |
| `agent/` | Serial conversation turns and model/tool continuation |
| `providers/` | Execute model Jobs |
| `tools/`, `mcp/` | Execute native and MCP tools through BUS |
| `channels/` | Transport messages and replies |

```bash
bun install
bun run test
bun run start -- @alice.magi
# or attach to asp:
bun run start -- @alice.magi http://127.0.0.1:42069 TOKEN
```

Without ASP arguments, the command opens a terminal chat. With ASP arguments,
it listens for session events and sends replies through ASP. State lives in
`~/.magi/alice`, derived from the handle; an existing `~/.magi/ts-magi/alice`
workspace is still opened, and a caller can point at another directory with the
`workspace` option. The handle comes from the command line and everything else
from that workspace — the process reads no environment variables.

Provider settings live in that workspace and the operator's app writes them
through ASP (`provider.name`, `provider.api_key`, `provider.model`,
`provider.base_url`). A fresh MAGI has none until they are saved in the app, and
nothing reads them from the environment. Provider models and protocols are
handled by `@earendil-works/pi-ai`; the desktop app offers OpenAI, Anthropic,
MiniMax, DeepSeek, and a custom OpenAI-compatible HTTPS endpoint (HTTP is
allowed for localhost) — for a custom endpoint, choose `custom` and supply base
URL, model ID and API key there. The Python LiteLLM dependency is not used.
`/exit` stops terminal chat; Ctrl-C stops ASP mode. Telegram runs when the
workspace has a `telegram.bot_token` setting; the channel is the Chat SDK's
Telegram adapter (`chat`, `@chat-adapter/telegram`) in long-polling mode, so
the protocol is not ours — the worker only turns an update into a `ChatNotify`
and a delivery into a post. In a group it hears only what addresses it: an @
mention or a reply to one of its messages.
Edit `<workspace>/prompts/agent/AGENT.md`, `compaction.md`, or
`skills_block.md` to override the managed agent prompts.

BUS owns SQLite Books, durable Jobs, and the live tool catalog through Bun
SQLite and in-memory state. Every Book declares the table it owns right in
`bus/books/` (the job queue in `bus/jobs/jobBoard.ts`);
`bun run db:generate` turns a table edit into the SQL under `bus/drizzle/`,
which the runtime applies on boot, and Books query through Drizzle rather than
hand-written SQL. Workers poll independently and communicate through
BUS using `ChatNotify`,
`CallLLMJob`, `RunToolJob`, `RunTaskNotify`, `ChangeProviderNotify`, and
`DeliveryNotify`. Agent turns are serial per conversation. Provider and tool
errors are saved on their Jobs. Provider changes are verified before becoming
active, ASP reconnects and acknowledges durable session events, long histories
are compacted, and active memories and workspace Skills are injected into the
agent context.

Errors reach the operator, never a log file. A component that knows the conversation
delivers the failure into it; a component that claimed a job writes the error into the
job result, and the agent that published the job passes it on. A component that only
sees trouble of its own — an MCP server that cannot connect, a worker that keeps
throwing — sends it to the conversation the operator last spoke in
(`home.conversation_id`, through `bus.publishNotice`). Only a process that cannot start
at all writes to stderr.

This runtime covers the local agent path, ASP sessions, Telegram text messages,
memory and contact tools, Skills, context compaction, recurring or manually
triggered tasks, foreground/background shell processes, and dynamically
configured stdio/SSE/Streamable-HTTP MCP tools. Provider routing supports the
OpenAI-compatible providers in the desktop picker plus Anthropic's native
Messages API.

The desktop packages Bun; the app starts one `magi` per agent, from that agent's own checkout.

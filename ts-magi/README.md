# ts-magi

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
MAGI_API_KEY=... bun run start -- @alice.magi
# or attach to magi-asp:
MAGI_API_KEY=... bun run start -- @alice.magi http://127.0.0.1:42069 TOKEN
```

Without ASP arguments, the command opens a terminal chat. With ASP arguments,
it listens for session events and sends replies through ASP. State lives in
`~/.magi/ts-magi/alice` by default, separate from the old Python database;
set `MAGI_WORKSPACE` to use another directory. `MAGI_MODEL` defaults to
`gpt-4.1-mini`. `MAGI_API_BASE` selects an OpenAI-compatible `/chat/completions`
endpoint. The desktop provider picker routes are supported directly without
the Python LiteLLM dependency.
`/exit` stops terminal chat; Ctrl-C stops ASP mode. Set
`MAGI_TELEGRAM_BOT_TOKEN` to poll Telegram and deliver text replies there.
Edit `<workspace>/prompts/agent/AGENT.md`, `compaction.md`, or
`skills_block.md` to override the managed agent prompts.

BUS owns SQLite Books, durable Jobs, and the live tool catalog through Bun
SQLite and in-memory state. Workers poll independently and communicate through
BUS using `ChatNotify`,
`CallLLMJob`, `RunToolJob`, `RunTaskNotify`, `ChangeProviderNotify`, and
`DeliveryNotify`. Agent turns are serial per conversation. Provider and tool
errors are saved on their Jobs. Provider changes are verified before becoming
active, ASP reconnects and acknowledges durable session events, long histories
are compacted, and active memories and workspace Skills are injected into the
agent context.

This runtime covers the local agent path, ASP sessions, Telegram text messages,
memory and contact tools, Skills, context compaction, recurring or manually
triggered tasks, foreground/background shell processes, and dynamically
configured stdio/SSE/Streamable-HTTP MCP tools. Provider routing supports the
OpenAI-compatible providers in the desktop picker plus Anthropic's native
Messages API. On first use it copies compatible Books and workspace assets from
an existing Python workspace into its separate TypeScript workspace.

The desktop packages Bun and ASP starts `ts-magi`.

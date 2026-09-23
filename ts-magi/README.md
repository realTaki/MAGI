# ts-magi

TypeScript MAGI follows the module names and responsibilities of `py-magi`:
`bus`, `agent`, `providers`, `tools`, `channels`, and the `magi.ts` composition root.

```bash
bun install
bun test
MAGI_API_KEY=... bun run start -- @alice.magi
# or attach to magi-asp, matching py-magi's command arguments:
MAGI_API_KEY=... bun run start -- @alice.magi http://127.0.0.1:42069 TOKEN
```

The command opens a terminal chat. State lives in `~/.magi/alice` by default;
set `MAGI_WORKSPACE` to use another directory. `MAGI_MODEL` defaults to
`gpt-4.1-mini`. `MAGI_API_BASE` selects a compatible `/chat/completions` endpoint.
`/exit` stops the process.

BUS owns SQLite Books and durable Jobs through Bun SQLite. Agent, provider, tools, and channel
workers communicate through `ChatNotify`, `CallLLMJob`, `RunToolJob`, and
`DeliveryNotify`. Agent turns are serial per conversation. Provider and tool
errors are saved on their Jobs.

This rewrite covers the local agent path and ASP session messages. Python's
Telegram, task scheduler, memory/contact Books, and context compaction are
not yet implemented.

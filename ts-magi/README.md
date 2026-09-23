# ts-magi

TypeScript MAGI follows the module names and responsibilities of `py-magi`:
`bus`, `agent`, `providers`, `tools`, `channels`, and the `magi.ts` composition root.

| py-magi | ts-magi | Responsibility |
| --- | --- | --- |
| `magi.py` | `magi.ts` | Compose one runtime and its workers |
| `bus/bus.py`, `bus/firmware` | `bus/bus.ts`, `bus/firmware` | Store Books and durable Jobs |
| `agent/worker.py`, `agent/conversations.py` | `agent/worker.ts`, `agent/conversations.ts` | Serial conversation turns and model/tool continuation |
| `providers/client.py`, `providers/worker.py` | `providers/client.ts`, `providers/worker.ts` | Execute model Jobs |
| `tools/registry.py`, `tools/worker.py` | `tools/registry.ts`, `tools/worker.ts` | Publish and execute native tools |
| `channels/asp`, `channels/telegram` | `channels/asp`, `channels/telegram` | Transport messages and replies |

```bash
bun install
bun run test
MAGI_API_KEY=... bun run start -- @alice.magi
# or attach to magi-asp, matching py-magi's command arguments:
MAGI_API_KEY=... bun run start -- @alice.magi http://127.0.0.1:42069 TOKEN
```

Without ASP arguments, the command opens a terminal chat. With ASP arguments,
it listens for session events and sends replies through ASP. State lives in
`~/.magi/ts-magi/alice` by default, separate from Python's database;
set `MAGI_WORKSPACE` to use another directory. `MAGI_MODEL` defaults to
`gpt-4.1-mini`. `MAGI_API_BASE` selects an OpenAI-compatible `/chat/completions`
endpoint. The Python provider picker and LiteLLM routes are not ported.
`/exit` stops terminal chat; Ctrl-C stops ASP mode. Set
`MAGI_TELEGRAM_BOT_TOKEN` to poll Telegram and deliver text replies there.
Edit `<workspace>/prompts/agent/AGENT.md` to override the default agent prompt.

BUS owns SQLite Books and durable Jobs through Bun SQLite. Agent, provider, tools, and channel
workers communicate through `ChatNotify`, `CallLLMJob`, `RunToolJob`, and
`DeliveryNotify`. Agent turns are serial per conversation. Provider and tool
errors are saved on their Jobs.

This rewrite covers the local agent path, ASP sessions, and Telegram text
messages. Python's task scheduler, memory/contact Books, background shell
processes, and context compaction are not yet implemented.

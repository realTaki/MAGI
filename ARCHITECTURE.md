# MAGI Architecture

The running system has three parts. The desktop is the operator's machine: it
starts ASP and runs every MAGI from that MAGI's own branch (`magi/eva-000`,
checked out at `~/.magi/eva-000/MAGI`). ASP serves as the central channel for
their shared chats: it tracks participants and relays events between them,
and never starts a process. Each MAGI is its own Node 24 process with its own BUS. Within that process, Books and Jobs provide a
single boundary for persistent state and coordination, so components depend
on the BUS rather than directly on one another.

```text
apps/shell/            Electron. Clones the repo, opens the window.
apps/user/              Operator UI and the local backend that starts ASP.
apps/asp/              Node 24. HTTP and WebSocket on 127.0.0.1:42069.
apps/eva/             Node 24. One process per MAGI: its entry and supervisor.
packages/              The runtime's packages: bus, agent, providers, built-in-tools, mcp, channel-*.
```

The installed package contains the shell plus Node.js 24 and npm. It
does not contain a Python runtime. On first launch the shell clones this
repository to `~/.magi/MAGI`. Later launches keep that checkout.

## Canonical terminology and names

This section is the repository's sole terminology authority. Code, persisted
data, HTTP payloads, events, tools, and documentation must use these names.

| Concept | Canonical name | Rule |
| --- | --- | --- |
| A stream of participants and messages | **chat** | Use `chat` in code, file names, APIs, tables, and prose. It is the same resource in App, ASP, and MAGI. |
| Chat identifier | `chat_id` / `chatId` | Use snake case in wire payloads and SQLite; camel case for TypeScript locals and parameters. |
| One item sent in a chat | **message** | Use `message` for the payload and durable record. `msg` is only acceptable for a short local variable. |
| A relay notification about a chat | **chat event** | Event types are `chat.message`, `chat.invited`, `chat.joined`, and so on; identifiers are `event_id`. |
| App presentation mode | `kind: "bot" | "group"` | This is App-only UI metadata for a private chat or group chat. ASP and MAGI operate on chats without a second conversation type. |
| MAGI address | `handle` | A MAGI address such as `@eva-000.magi`; it is not a chat ID. |

`conversation`, `conversation_id`, `session`, and `session_id` are not names
for a chat anywhere in this repository. The only exception is a genuine
short-lived authentication or browser session, which must not identify a chat.

The canonical ASP surface is `/chats`: the App creates and lists chats at
`POST`/`GET /chats`; the lower-level open operation is `POST /chats/open`; and
participant operations use `/chats/:chat_id/...`.

| Platform term | Meaning |
| --- | --- |
| **MAGI** | Modular Agentic Genesis Intelligences, the project name. One MAGI is one governable agent and its Node 24 runtime in `apps/eva/` and `packages/`; the plural refers to independent agents working together. |
| **ASP** | The local chat server in `apps/asp/`. It registers agents and relays events; it never starts a process and does not reason. |
| **Desktop** | The Electron shell and operator UI. It owns the checkout, transcript, and provider key. |
| **BUS** | The durable boundary inside one MAGI process: Books and Jobs in `packages/bus/`. |
| **Book** | Durable workspace records such as chats, messages, memory, skills, contacts, and prompts. |
| **Job** | A durable `publish -> claim -> result` item. Chat, model calls, tool calls, and delivery are Jobs. |
| **EVA** | The handle naming pattern. ASP assigns `eva-000`, then `eva-001`; its address is `@eva-000.magi`. |
| **Workspace** | One MAGI's directory, normally `~/.magi/<name>`, containing that MAGI's `MAGI/` source checkout. |
| **Agent branch** | `magi/<name>` — the branch a MAGI runs from. The Desktop creates its Git worktree and merges changes into it on demand. |

## Who owns which files

| Path | Owner | What it is |
| --- | --- | --- |
| `~/.magi/MAGI` | desktop | Git checkout of this repository. |
| `~/.magi/app/chat.sqlite` | desktop | The operator's transcript. |
| `~/.magi/app/provider.json` | desktop | Provider, model, and API key. |
| `~/.magi/asp/asp.sqlite` | ASP | Chats, participants, and relay events. |
| `~/.magi/<name>` | that MAGI | Workspace. Books and Job history. |

An older workspace at `~/.magi/ts-magi/<name>` is still opened when
`~/.magi/<name>` does not exist yet.

## Desktop startup

The shell puts Node.js 24 and npm on disk, then clones this repository to
`~/.magi/MAGI`. What it asks the backend to do, in order (the bridge contract is
documented in `apps/user/main/index.ts`):

1. `prepare()` — check the lockfiles are there, then, in a managed checkout with
   no `node_modules` yet, run `npm install` at the root and `npm ci` in `apps/asp/`,
   and `npm ci` plus `npm run build` in `apps/user/`. It answers with the
   interface entry point.
2. `start()` — run `apps/asp/main.ts` with Node 24 and wait for `GET /health` to
   report this runtime. `dispose()` releases reloadable resources; `shutdown()`
   stops ASP.
3. With no MAGI registered yet, the backend creates the first three through ASP
   (`eva-000`, `eva-001`, `eva-002`), starts them, and names them MELCHIOR,
   BALTHASAR, CASPER as they come online. One that never comes online does not
   fail startup.

Closing the window, or reloading the client, never stops ASP — only quitting the
application does, which is what `before-quit` in `apps/shell/main.mjs` and the
`shutdown()` bridge method are for.

`apps/shell/` has no test tree, so this flow is a contract in prose rather than an
assertion. The other flows live with the tests that hold them, named below.

## ASP

`asp/main.ts` is the process entry. Node 24 runs the TypeScript directly.

The operator API (`/chats`, `/bots`, `/settings/provider`) requires the
desktop's Bearer token. The participant API (`/chats/open`,
`/chats/:chat_id/...`, WebSocket `/connect`) accepts a MAGI's Bearer token.

Creating a bot does not take a name or a model. ASP assigns `eva-000`, then
`eva-001`, and starts:

```text
npm start -- @eva-000.magi http://127.0.0.1:42069 <token>
```

The working directory is the checkout's `magi/`. A group chat starts
with only the operator. Inviting a MAGI sends `chat.invited`. That MAGI
joins on receipt.

ASP deletes a message event only after every intended recipient has
acknowledged that exact event. The desktop stores the long-term transcript
itself and acknowledges ASP after that write. ASP does not keep the operator's
history.

`PUT /settings/provider` forwards a complete provider update to connected
MAGI and reports which ones synced. ASP does not save a new copy of the key.

Asserted in `asp/test/`: creating a chat, relaying with exact recipient
acks, and the update being transient.

## A MAGI process

`apps/eva/eva.ts` composes one BUS and the workers that poll it. Workers do not
call each other. They publish Jobs and claim Jobs — asserted in
`magi/test/agent.test.ts`, `tools.test.ts`, and `tasks.test.ts`.

| Worker | Reads | Writes |
| --- | --- | --- |
| Agent | `ChatNotify` | `CallLLMJob`, `RunToolJob` |
| Providers | `CallLLMJob` | the model result |
| Tools | `RunToolJob` | the tool result |
| ASP channel | chat events | `ChatNotify`, `DeliveryNotify` replies |
| CLI | terminal lines | `ChatNotify` |
| Telegram | bot updates | `ChatNotify` |
| Tasks | due tasks, `RunToolJob` | `ChatNotify` for a fired task, the tool result |
| MCP | `ChangeMcpServerNotify` | tools from configured servers |

Channels stop at translating. The Telegram one is the Chat SDK's Telegram
adapter in polling mode (`magi/channels/telegram/worker.ts`): the SDK owns long
polling, offsets, retries, and rendering, the worker only publishes a
`ChatNotify` or posts a `DeliveryNotify`, and adapter trouble is reported
through `health()` so the supervisor can tell the operator. A group is only
heard when the MAGI is addressed — an @ mention, or a reply to one of its
messages; a DM is the operator's own chat. What does arrive is recorded like
ASP's: the chat, the message, the sender as a contact, and both of them
as members.

Books in `packages/bus/books/` hold chats, messages, memory,
skills, tasks, contacts, contact notes, prompts, MCP servers, and the tool
catalog. SQLite files are `memories/magi.db` and `jobs/magi.db` inside the
workspace. Node owns SQLite through `better-sqlite3`. Each Book declares the
table it owns next to its queries (`packages/bus/books/`; job queue:
`packages/bus/jobs/jobBoard.ts`); `npm run db:generate` writes the SQL
migrations under `packages/bus/drizzle/`, which the runtime applies on boot.
Errors are delivered, not logged: into the chat the failure belongs to, into
the job result the agent will surface, or — for a component that only sees trouble of
its own — into the operator's home chat (`home.chat_id`, via
`bus.publishNotice`).

Without ASP arguments, `npm start -- @alice.magi` is a terminal chat.
With a base URL and token, the process attaches to ASP and does not take over
the operator's files.

## Older notes

Older notes live in git history: the ones that map the retired Python package,
and `MAGI-BUS 架构设计书.md`, which recorded an earlier baseline. Where they
name Python modules, they are not a map of this tree.

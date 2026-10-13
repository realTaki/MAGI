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
apps/user/              Operator process: BUS manager and UI. The backend worker is packages/webapp.
apps/asp/              Node 24. HTTP and WebSocket on 127.0.0.1:42069.
apps/eva/             Node 24. One process per MAGI: its entry and supervisor.
packages/              The runtime's packages: bus, manager, asp-server, webapp, agent, providers, built-in-tools, mcp, channel-*.
```

The installed package contains the shell plus Node.js 24 and npm. It
does not contain a Python runtime. On first launch the shell clones this
repository to `~/.magi/shell`. Later launches keep that checkout.

Before starting User in a fresh managed worktree, the shell installs the root
lockfile with `npm ci --ignore-scripts`, then runs `build:process` in `apps/user`
when `dist/user.js` is missing. This bootstrap happens before User can prepare
the remaining desktop components.

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
| `~/.magi/shell` | Shell | Source checkout and Git/worktree authority; Shell installer source. |
| `~/.magi/shell/cache` | Shell | Generated npm, Chromium, Electron download and builder caches. |
| `~/.magi/user/chat.sqlite` | desktop | The operator's transcript. |
| `~/.magi/user/provider.json` | desktop | Provider, model, and API key. |
| `~/.magi/asp/asp.sqlite` | ASP | Chats, participants, and relay events. |
| `~/.magi/<name>` | that MAGI | Workspace. Books and Job history. |

An older workspace at `~/.magi/ts-magi/<name>` is still opened when
`~/.magi/<name>` does not exist yet.

## Desktop startup

The shell puts Node.js 24 and npm on disk, then clones this repository to
`~/.magi/shell`. It starts User once as `node dist/user.js` from `apps/user/`
(unpackaged: `apps/shell/../user/dist/user.js`). Reloads do not kill User. Quit
sends SIGTERM. User then spawns ASP and Eva through the same supervisor. What
User does, in order (the worker is `packages/webapp`):

1. `prepare()` — check the lockfiles are there, then, in a managed checkout with
   no `node_modules` yet, run `npm install` at the root and `npm ci` in `apps/asp/`,
   and `npm ci` plus `npm run build` in `apps/user/`. It answers with the
   interface entry point.
2. `start()` — run `apps/asp/dist/asp.js` with Node 24 and wait for `GET /health` to
   report this runtime.
3. With no MAGI registered yet, the backend creates the first three through ASP
   (`eva-000`, `eva-001`, `eva-002`), starts them, and names them MELCHIOR,
   BALTHASAR, CASPER as they come online. One that never comes online does not
   fail startup.

Closing the window, or reloading the client, never stops User or ASP — only
quitting the application does, which is `before-quit` stopping every supervised
app in `apps/shell/main.mjs`.

Shell handles `request_rebuild` links directly. It compiles the target's actual
worktree, then replaces only that app using its original cwd, args, and env.
`self` includes local source edits; `app` and `asp` merge the requesting EVA's
branch into the registered User or ASP worktree. User rebuild includes both
backend and UI. User retirement does not stop ASP or EVA; the replacement
adopts their existing supervisor handles. Build errors leave the old app running;
restart errors remain failed, without automatic rollback or retry. A ready IPC
signal means the new app manager has initialized (ASP has bound its server);
it does not imply an EVA has connected to ASP or passed all worker health checks.
Shell retains the phase/result through `runtime.rebuildStatus` and emits
`runtime.rebuild` events. The tool's OS acceptance is only a request.

`apps/shell/test/` verifies process isolation, new compiled output, ready and
failure reporting, and User retirement. The other flows live with the tests that hold them, named below.

## ASP

`apps/asp/asp.ts` is the process entry, the same shape as `apps/eva/eva.ts`. Both compile through `tsc -b` and run as `dist/asp.js` / `dist/eva.js`.

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
| Agent | `MessageDeliveryJob` (anyone else's) | `CallLLMJob`, `RunToolJob`, `MessageDeliveryJob` (its own) |
| Providers | `CallLLMJob` | the model result |
| Tools | `RunToolJob` | the tool result |
| ASP channel | chat events | `MessageDeliveryJob` (what it heard, and this MAGI's replies) |
| CLI | terminal lines | `MessageDeliveryJob` |
| Telegram | bot updates | `MessageDeliveryJob` |
| Tasks | due tasks, `RunToolJob` | `MessageDeliveryJob` for a fired task, the tool result |
| MCP | `ChangeMcpServerNotify` | tools from configured servers |

Everything a chat and this MAGI say to each other is one job, `MessageDeliveryJob`, and
its author decides who acts: what someone else said is the agent's to answer, this MAGI's
own words are the chat channel's to post. The payload carries only the durable message
id — a worker reads the author, chat, and text from that row — and the message is
recorded by the job's `send()`, so no worker writes the message book.

Channels stop at translating. The Telegram one is the Chat SDK's Telegram
adapter in polling mode (`magi/channels/telegram/worker.ts`): the SDK owns long
polling, offsets, retries, and rendering, the worker only records what it
heard and posts what this MAGI said, and adapter trouble is reported
through `health()` so the supervisor can tell the operator. A group is only
heard when the MAGI is addressed — an @ mention, or a reply to one of its
messages; a DM is the operator's own chat. What does arrive is recorded like
ASP's: the chat, the message, the sender as a contact, and both of them
as members.

Books in `packages/bus/books/` hold chats, messages, memory,
skills, tasks, contacts, contact notes, prompts, MCP servers, and the tool
catalog. SQLite files are `memories/books.db` and `logs/jobs.db` inside the
workspace. Node owns SQLite through `better-sqlite3`. Each Book declares the
table it owns next to its queries (`packages/bus/books/`; job queue:
`packages/bus/jobs/jobBoard.ts`); `npm run db:generate` writes the SQL
migrations under `packages/bus/drizzle/`, which the runtime applies on boot.
Errors are delivered, not logged: into the chat the failure belongs to, into
the job result the agent will surface, or — for a component that only sees trouble of
its own — into the operator's home chat (`home.chat_id`, via
`messageDelivery.notify`).

Without ASP arguments, `npm start -- @alice.magi` is a terminal chat.
With a base URL and token, the process attaches to ASP and does not take over
the operator's files.

## Older notes

Older notes live in git history: the ones that map the retired Python package,
and `MAGI-BUS 架构设计书.md`, which recorded an earlier baseline. Where they
name Python modules, they are not a map of this tree.

# MAGI Architecture

The running system has three host processes plus any extra EVA children. The
desktop operator process (still launched from `apps/user`, directory name
`shell` on disk) hosts the human UI and the host MAGI **Balthasar**
(`@eva-000.magi`). ASP is the chat relay and the host MAGI **Casper**
(`@eva-001.magi`). **Melchior** (`@eva-002.magi`) is the one classic spawned
EVA child, running `apps/eva/dist/eva.js` from `~/.magi/eva-002/MAGI`. ASP
never starts a process. Within each process, Books and Jobs are the boundary
for persistent state and coordination, so workers depend on the BUS rather
than on one another.

```text
apps/shell/            Electron. Clones the repo, opens the window. App dir stays shell.
apps/user/              Operator process: Webapp plus Balthasar's agent workers. Backend worker is packages/webapp.
apps/asp/              Node 24. HTTP and WebSocket on 127.0.0.1:42069, plus Casper's agent workers.
apps/eva/             Node 24. Spawned MAGI entry (Melchior, and any later eva-NNN).
packages/              The runtime's packages: bus, manager, asp-server, webapp, agent, providers, built-in-tools, mcp, channel-*.
```

The installed package contains the shell plus Node.js 24 and npm. It
does not contain a Python runtime. On first launch the shell clones this
repository to `~/.magi/shell/MAGI`. Later launches keep that checkout.

Before starting User in a fresh managed clone, the shell installs the root
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
| Operator speech | `@user` / `@user.magi` | Human messages from the webapp. Not the BUS owner of the operator process. |
| Host MAGI (Shell) | `@eva-000` / `@eva-000.magi` | Balthasar. Lives in the operator process. No `eva.js` child. |
| Host MAGI (ASP) | `@eva-001` / `@eva-001.magi` | Casper. Lives in the ASP process. No `eva.js` child. |
| Spawned EVA | `@eva-002` / `@eva-002.magi` | Melchior. The default society's only `apps/eva` child. |

`conversation`, `conversation_id`, `session`, and `session_id` are not names
for a chat anywhere in this repository. The only exception is a genuine
short-lived authentication or browser session, which must not identify a chat.

The canonical ASP surface is `/chats`: the App creates and lists chats at
`POST`/`GET /chats`; the lower-level open operation is `POST /chats/open`; and
participant operations use `/chats/:chat_id/...`.

| Platform term | Meaning |
| --- | --- |
| **MAGI** | Modular Agentic Genesis Intelligences, the project name. One MAGI is one governable agent. Balthasar and Casper run inside Shell and ASP; Melchior and later `eva-NNN` run `apps/eva/` plus `packages/`. |
| **ASP** | The local chat server in `apps/asp/`. It registers agents and relays events; it never starts a process. The same process hosts Casper (`@eva-001.magi`). |
| **Desktop** | The Electron shell and operator UI. It owns the checkout, transcript, and provider key. The operator process also hosts Balthasar (`@eva-000.magi`). |
| **BUS** | The durable boundary inside one MAGI process: Books and Jobs in `packages/bus/`. |
| **Book** | Durable workspace records such as chats, messages, memory, skills, contacts, and prompts. |
| **Job** | A durable `publish -> claim -> result` item. Chat, model calls, tool calls, and delivery are Jobs. |
| **EVA** | The handle naming pattern. Addresses are `@eva-000.magi`, `@eva-001.magi`, `@eva-002.magi`. Only `@eva-002` and later extra MAGIs are spawned `eva.js` children. |
| **Workspace** | One MAGI's directory, normally `~/.magi/<name>`, containing that MAGI's `MAGI/` source checkout. |
| **Agent branch** | `magi/<name>` — the branch a MAGI runs from. The Desktop clones the GitHub remote into that MAGI's workspace and merges remote changes into it on demand. |

## Who owns which files

| Path | Owner | What it is |
| --- | --- | --- |
| `~/.magi/shell/MAGI` | Shell | Shell clone on branch `magi/shell`; installer source. |
| `~/.magi/shell/cache` | Shell | Generated npm, Chromium, Electron download and builder caches. Sibling of MAGI. |
| `~/.magi/user/MAGI` | desktop | User clone on branch `magi/user`. |
| `~/.magi/user/chat.sqlite` | desktop | The operator's transcript. |
| `~/.magi/user/memories/books.db` | desktop | Operator BUS (handle `@eva-000.magi`, Balthasar): provider, model, and API key (SettingsBook). |
| `~/.magi/asp/MAGI` | ASP | ASP clone on branch `magi/asp`. |
| `~/.magi/asp/asp.sqlite` | ASP | Chats, participants, and relay events. |
| `~/.magi/<name>` | that MAGI | Workspace. Books and Job history. |
| `~/.magi/<name>/MAGI` | that MAGI | Independent clone on branch `magi/<name>`. |

## Desktop startup

The shell puts Node.js 24 and npm on disk, then clones this repository to
`~/.magi/shell/MAGI`. It starts User once as `node dist/user.js` from `apps/user/`
(unpackaged: `apps/shell/../user/dist/user.js`). Reloads do not kill User. Quit
closes process admission, drains/stops owned apps and reaps owned commands. User
prepares ASP and EVA source trees; Shell starts them through its supervisor. What
User does, in order (the worker is `packages/webapp`):

1. `prepare()` — check the lockfiles, ask Shell to validate/repair managed
   dependency installations against their completion markers and hidden locks,
   then build the User interface when needed. Shell also prepares the User
   process before its first start. It answers with the interface entry point.
2. `start()` — run `apps/asp/dist/asp.js` with Node 24 and wait for `GET /health` to
   report this runtime.
3. The default society is three MAGIs. Balthasar (`@eva-000.magi`) is this
   operator process. Casper (`@eva-001.magi`) is the ASP process. Melchior
   (`@eva-002.magi`) is the only MAGI started as `eva.js`. Naming is best
   effort: a MAGI that never comes online does not fail startup.

Closing the last window quits Shell and stops its owned runtime. Reloading the
client keeps User and ASP running. `before-quit` stops every supervised
app in `apps/shell/main.mjs`.

Shell handles `request_rebuild` links directly. It compiles the target's actual
clone, then replaces only that app using its original cwd, args, and env.
`self` includes local source edits; `app` and `asp` push the requesting EVA's
branch and merge it in the User or ASP clone. User rebuild includes both
backend and UI. User retirement does not stop ASP or EVA; the replacement
adopts their existing supervisor handles. Build errors leave the old app running;
restart errors remain failed, without automatic rollback or retry. A ready IPC
signal means the new app manager has initialized (ASP has bound its server);
it does not imply an EVA has connected to ASP or passed all worker health checks.
Shell retains the phase/result through `runtime.rebuildStatus` and emits
`runtime.rebuild` events. The tool's OS acceptance is only a request.

Process ownership for rebuild still keys the operator process as `@user.magi`
and ASP as `@asp.magi`. Moving those keys onto `@eva-000.magi` and
`@eva-001.magi` is a follow-up. The Electron main process stays a thin shell.

`apps/shell/test/` verifies process isolation, new compiled output, ready and
failure reporting, and User retirement. The other flows live with the tests that hold them, named below.

## ASP

`apps/asp/asp.ts` is the process entry, the same shape as `apps/eva/eva.ts`. Both compile through `tsc -b` and run as `dist/asp.js` / `dist/eva.js`.

The operator API (`/chats`, `/bots`, `/settings/provider`) requires the
desktop's Bearer token. The participant API (`/chats/open`,
`/chats/:chat_id/...`, WebSocket `/connect`) accepts a MAGI's Bearer token.

Creating a bot does not take a name or a model. ASP assigns the next free
`eva-NNN` (Casper already occupies `eva-001`), and the desktop starts only
spawned children:

```text
npm start -- @eva-002.magi http://127.0.0.1:42069 <token>
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

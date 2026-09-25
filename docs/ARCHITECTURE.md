---
title: Architecture
description: How the desktop, ASP, and one MAGI process fit together.
permalink: /architecture/
---

# MAGI Architecture

The running system has three parts. The desktop is the operator's machine: it
starts ASP and runs every MAGI from that MAGI's own branch (`magi/eva-000`,
checked out at `~/.magi/eva-000/MAGI`). ASP serves as the central channel for
their shared sessions: it tracks participants and relays events between them,
and never starts a process. Each MAGI is its own Bun process with its own BUS. Within that process, Books and Jobs provide a
single boundary for persistent state and coordination, so components depend
on the BUS rather than directly on one another.

```text
shell/                 Electron. Clones the repo, opens the window.
app/                   Operator UI and the local backend that starts ASP.
asp/                   Node 24. HTTP and WebSocket on 127.0.0.1:42069.
magi/                  Bun. One process per MAGI. BUS, workers, tools.
```

The installed package contains the shell plus Node.js 24, npm, and Bun. It
does not contain a Python runtime. On first launch the shell clones this
repository to `~/.magi/MAGI`. Later launches keep that checkout.

## Who owns which files

| Path | Owner | What it is |
| --- | --- | --- |
| `~/.magi/MAGI` | desktop | Git checkout of this repository. |
| `~/.magi/app/chat.sqlite` | desktop | The operator's transcript. |
| `~/.magi/app/provider.json` | desktop | Provider, model, and API key. |
| `~/.magi/asp/asp.sqlite` | ASP | Sessions, participants, and relay events. |
| `~/.magi/<name>` | that MAGI | Workspace. Books and Job history. |

An older workspace at `~/.magi/ts-magi/<name>` is still opened when
`~/.magi/<name>` does not exist yet.

## ASP

`asp/main.ts` is the process entry. Node 24 runs the TypeScript directly.

The operator API (`/conversations`, `/bots`, `/settings/provider`) requires
the desktop's Bearer token. The participant API (`/sessions`, WebSocket
`/connect`) accepts a MAGI's Bearer token.

Creating a bot does not take a name or a model. ASP assigns `eva-000`, then
`eva-001`, and starts:

```text
bun run start -- @eva-000.magi http://127.0.0.1:42069 <token>
```

The working directory is the checkout's `magi/`. A group conversation starts
with only the operator. Inviting a MAGI sends `session.invited`. That MAGI
joins on receipt.

ASP deletes a message event only after every intended recipient has
acknowledged that exact event. The desktop stores the long-term transcript
itself and acknowledges ASP after that write. ASP does not keep the operator's
history.

`PUT /settings/provider` forwards a complete provider update to connected
MAGI and reports which ones synced. ASP does not save a new copy of the key.

## A MAGI process

`magi/magi.ts` composes one BUS and the workers that poll it. Workers do not
call each other. They publish Jobs and claim Jobs.

| Worker | Reads | Writes |
| --- | --- | --- |
| Agent | `ChatNotify` | `CallLLMJob`, `RunToolJob` |
| Providers | `CallLLMJob` | the model result |
| Tools | `RunToolJob` | the tool result |
| ASP channel | session events | `ChatNotify`, `DeliveryNotify` replies |
| CLI | terminal lines | `ChatNotify` |
| Telegram | bot updates | `ChatNotify` |
| Tasks | due tasks | `RunTaskNotify` |
| MCP | `ChangeMcpServerNotify` | tools from configured servers |

Channels stop at translating. The Telegram one is the Chat SDK's Telegram
adapter in polling mode (`magi/channels/telegram/worker.ts`): the SDK owns long
polling, offsets, retries, and rendering, the worker only publishes a
`ChatNotify` or posts a `DeliveryNotify`, and adapter trouble is reported
through `health()` so the supervisor can tell the operator. A group is only
heard when the MAGI is addressed — an @ mention, or a reply to one of its
messages; a DM is the operator's own chat. What does arrive is recorded like
ASP's: the conversation, the message, the sender as a contact, and both of them
as members.

Books in `magi/bus/books/` hold conversations, messages, memory,
skills, tasks, contacts, contact notes, prompts, MCP servers, and the tool
catalog. SQLite files are `memories/magi.db` and `logs/magi.db` inside the
workspace. Bun owns that SQLite. Each Book declares the table it owns next to
its queries (`magi/bus/books/`; job queue:
`magi/bus/jobs/jobBoard.ts`); `bun run db:generate` writes the SQL
migrations under `magi/bus/drizzle/`, which the runtime applies on boot.
Errors are delivered, not logged: into the conversation the failure belongs to, into
the job result the agent will surface, or — for a component that only sees trouble of
its own — into the operator's home conversation (`home.conversation_id`, via
`bus.publishNotice`).

Without ASP arguments, `bun run start -- @alice.magi` is a terminal chat.
With a base URL and token, the process attaches to ASP and does not take over
the operator's files.

## Older notes

Older notes that map the retired Python package live in git history. The
design book [`MAGI-BUS 架构设计书.md`](MAGI-BUS%20架构设计书.md) records an
earlier baseline. Where it names Python modules, it is not a map of this tree.

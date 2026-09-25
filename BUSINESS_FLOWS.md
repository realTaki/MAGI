# MAGI business flows

This is what the current code does.

## Desktop startup

1. The Electron shell prepares Node.js 24, npm and Bun, and clones the repository to `~/.magi/MAGI`.
2. The local backend runs `npm ci` in `asp/`, `bun install` in `magi/`, then builds `app`.
3. The backend runs `asp/main.ts` with Node 24 and waits for `GET /health` to succeed.
4. If there is no MAGI yet, the backend creates `eva-000`, `eva-001`, `eva-002`, and once they are online tries to write the nicknames MELCHIOR, BALTHASAR, CASPER. One that is not online does not fail startup.

The shell owns the window, the clone, and forwarding calls to `app/main`. Closing the interface and loading the backend again must not kill the ASP child process. Quitting the app ends ASP.

## Creating a conversation

`POST /conversations` accepts only `{ "kind": "bot" | "group" }`, and only the operator's Bearer token can call it.

- `bot`: ASP allocates the next `eva-NNN`, writes it to its own database, then starts `magi/` with Bun. The desktop does not launch a MAGI itself.
- `group`: opens a conversation for the operator alone, and starts no process.

Inviting goes through `POST /conversations/{id}/members`. An invited MAGI receives `session.invited` and joins on its own. Everyone in a group has to be a MAGI that ASP already knows.

## Sending a message

1. The desktop writes the message into `~/.magi/app/chat.sqlite` first.
2. Then it hands it to ASP.
3. ASP keeps the event for every recipient that has not acknowledged it. A MAGI receives it over WebSocket `/connect`, writes it into its own workspace, then acknowledges that `event_id`.
4. Only once every intended recipient has acknowledged does ASP delete the message event.
5. A MAGI's reply travels back to the desktop through ASP. The desktop persists it first, then acknowledges.

ASP is not the long-term store of chat history. After a restart, the desktop reads the history from its own SQLite.

## Switching the model

The settings page writes provider, model and API key into `~/.magi/app/provider.json`. `PUT /settings/provider` forwards that one complete set of values to the MAGI that are connected. ASP does not save a new copy of the key. A MAGI applies `ChangeProviderNotify` in its own BUS and answers `agent.provider.updated` once it succeeds. The desktop retries later for MAGI that were not connected.

## One MAGI turn

The ASP channel, the terminal, or Telegram publishes text as a `ChatNotify`. The agent worker claims it and publishes a `CallLLMJob` when it needs the model, or a `RunToolJob` when it needs a tool. The providers worker and the tools worker each claim their jobs and write the result back. Text meant for the operator is published as a `DeliveryNotify` and sent by the matching channel. These workers never call each other.

# asp

Build with `npm run build` at the repository root, then run `node dist/asp.js`
from this directory (what the desktop app does). `npm test` runs the suite
against the compiled output.

Layout: `asp.ts` is the composition root, same shape as `apps/eva/eva.ts`. The
BUS is owned by Casper (`@eva-001.magi`). The process runs `asp-server` plus
the same agent workers as a MAGI (Agent, Tools, Skills, Memory, Providers,
Contacts). `@magi/asp-server` owns the operator HTTP, MAGI WebSocket, relay
tables, and `db/`. `npm run db:generate`
in `packages/asp-server` turns a table change into SQL under `db/drizzle/`,
which the worker applies when it opens the database. Rows go through drizzle
rather than hand-written SQL. App dir stays `asp`.

Desktop-only HTTP `/chats` and `/bots` require the
operator Bearer token. `POST /profile` and `GET /profiles` are how a MAGI
registers and reads public keys. Participant HTTP uses `/chats/open` and
`/chats/{chat_id}/...` with a participant Bearer token; WebSocket `/connect`
is MAGI's live channel.
SQLite is `~/.magi/asp/asp.sqlite`.

ASP persists chats, participants and relay events in SQLite. Recipients
acknowledge exact event IDs after saving them locally (`POST
/chats/{id}/events/ack` or a WebSocket `chat.ack`). A message event is
deleted only after every intended recipient has acknowledged it. The desktop's
long-term chat history lives in its own SQLite; ASP does not serve as that
history store.

ASP is **intranet by default**: it binds `127.0.0.1:42069`. MAGI attach with a Bearer for this origin. When a MAGI receives `chat.invited`, it joins immediately — no public-network approval and no config wizard.

ASP never starts a process. It keeps the roll of agents and their tokens; the
desktop app on this machine owns each MAGI's branch, clone and process.

`POST /chats` `{ "kind": "bot" | "group" }` does not take a MAGI name, model, or settings. For `bot`, ASP assigns the next free `eva-NNN` (Casper already occupies `eva-001` as this process), registers it as a managed agent, and returns `name`, the handle and the agent token on the create response. The desktop spawns `eva.js` only for handles that are not in-process hosts. `GET /agents` lists the same credentials for every registered agent, so a restarted app can pick the society back up. `group` opens a chat with the operator only.

Group profile **invite** uses:

- `GET /bots` — MAGI this operator can add
- `POST /chats/{chat_id}/members` `{ "handle" }` — invite that MAGI; it joins on receipt

Each MAGI keeps its provider key in its own secret store. ASP keeps the
public key that MAGI registers, and nothing else about the provider.

# asp

Run with `node main.ts` from this directory (what the desktop app does). Node 24
runs the TypeScript directly. `npm test` runs the suite.

Layout: `main.ts` is the composition root and process entry. `server/app.ts`
assembles the desktop operator routes and the shared chat HTTP and MAGI
WebSocket protocol. `operator-service.ts` projects desktop views and registers
agents; `service.ts` owns chat state and event delivery.
`store.ts` / `transport.ts` persist relay state and manage live connections;
`db/` owns the SQLite file. Its tables are declared in `db/schema.ts`, and
`npm run db:generate` turns a change there into SQL under `db/drizzle/`, which
the relay applies when it opens the database. Rows go through drizzle rather
than hand-written SQL.

Desktop-only HTTP `/chats`, `/bots`, and `/settings/provider` require the
operator Bearer token. Participant HTTP uses `/chats/open` and
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
desktop app on this machine owns each MAGI's branch, checkout and process.

`POST /chats` `{ "kind": "bot" | "group" }` does not take a MAGI name, model, or settings. For `bot`, ASP assigns `name` `eva-000`, then `eva-001`, …, registers it as a managed agent, and returns `name`, the handle and the agent token on the create response — that is what the caller runs the agent with (the app checks out branch `magi/<name>` at `~/.magi/<name>/MAGI`). `GET /agents` lists the same credentials for every registered agent, so a restarted app can pick the society back up. `group` opens a chat with the operator only.

Group profile **invite** uses:

- `GET /bots` — MAGI this operator can add
- `POST /chats/{chat_id}/members` `{ "handle" }` — invite that MAGI; it joins on receipt

The desktop app owns provider settings and the API key in `~/.magi/app/provider.json`.
ASP only forwards a complete update to MAGI; it never saves a new copy:

- `PUT /settings/provider` — transiently hands `agent.provider.update` to
  connected MAGI and reports `synced` / `failed`. The app retries when a MAGI
  comes online.
- `GET` and `DELETE /settings/provider/legacy` let the app copy an older
  `asp_settings.provider` value into its own file before removing that value.
- The MAGI side answers with `agent.provider.updated` after publishing the change
  into its own bus (persisted settings + live provider reconfiguration).

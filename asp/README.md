# asp

Run with `node main.ts` from this directory (what the desktop app does). Node 24
runs the TypeScript directly. `npm test` runs the suite.

Layout: `main.ts` is the composition root and process entry. `server/app.ts`
assembles the desktop operator routes and the shared session HTTP and MAGI
WebSocket protocol. `operator-service.ts` projects desktop views and starts
managed MAGI; `service.ts` owns session state and event delivery.
`store.ts` / `transport.ts` persist relay state and manage live connections;
`db/` owns the SQLite file.

Desktop-only HTTP `/conversations`, `/bots`, and `/settings/provider` require
the operator Bearer token. Shared HTTP `/sessions` accepts participant Bearer
tokens from desktop or MAGI; WebSocket `/connect` is MAGI's live channel.
SQLite is `~/.magi/asp/asp.sqlite`.

ASP persists sessions, participants and relay events in SQLite. Recipients
acknowledge exact event IDs after saving them locally (`POST
/sessions/{id}/events/ack` or a WebSocket `session.ack`). A message event is
deleted only after every intended recipient has acknowledged it. The desktop's
long-term chat history lives in its own SQLite; ASP does not serve as that
history store.

ASP is **intranet by default**: it binds `127.0.0.1:42069`. Spawned MAGI attach with a Bearer for this origin. When a MAGI receives `session.invited`, it joins immediately — no public-network approval and no config wizard.

`POST /conversations` `{ "kind": "bot" | "group" }` does not take a MAGI name, model, or settings. `bot` is ASP-side spawn only (the desktop never starts MAGI): ASP assigns `name` `eva-000`, then `eva-001`, …, starts that MAGI with the bundled Bun TypeScript runtime, and returns `name` on the create response. `group` opens a conversation with the operator only.

Group profile **邀请** uses:

- `GET /bots` — MAGI this operator can add
- `POST /conversations/{conversation_id}/members` `{ "handle" }` — invite that MAGI; it joins on receipt

The desktop app owns provider settings and the API key in `~/.magi/app/provider.json`.
ASP only forwards a complete update to MAGI; it never saves a new copy:

- `PUT /settings/provider` — transiently hands `agent.provider.update` to
  connected MAGI and reports `synced` / `failed`. The app retries when a MAGI
  comes online.
- `GET` and `DELETE /settings/provider/legacy` let the app copy an older
  `asp_settings.provider` value into its own file before removing that value.
- The MAGI side answers with `agent.provider.updated` after publishing the change
  into its own bus (persisted settings + live provider reconfiguration).

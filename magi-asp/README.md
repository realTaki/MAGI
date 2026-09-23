# magi-asp

Run with `python main.py` (what the desktop app does, from this directory) or the
`magi-asp` console script.

Layout: `main.py` is the composition root and process entry (`AspServer`,
`create_app`, `main`), `server/` holds the ASP itself — `app.py` for the HTTP/WS
routes, `service.py`/`store.py`/`transport.py`/`spawn.py`/`operator.py` for the
local-network layer MAGI processes join — and `db/` owns the sqlite file.

HTTP `/conversations` (operator plus-button), `/sessions` (MAGI wire), and WebSocket `/connect`. SQLite is `~/.magi/asp/asp.sqlite`. MAGI and the desktop are clients.

ASP persists sessions, participants and relay events in SQLite. Recipients
acknowledge exact event IDs after saving them locally (`POST
/sessions/{id}/events/ack` or a WebSocket `session.ack`). A message event is
deleted only after every intended recipient has acknowledged it. The desktop's
long-term chat history lives in its own SQLite; ASP does not serve as that
history store.

magi-asp is **intranet by default**: it binds `127.0.0.1:42069`. Spawned MAGI attach with a Bearer for this origin. When a MAGI receives `session.invited`, it joins immediately — no public-network approval and no config wizard.

`POST /conversations` `{ "kind": "bot" | "group" }` does not take a MAGI name, model, or settings. `bot` is ASP-side spawn only (the desktop never starts MAGI): ASP assigns `name` `eva-000`, then `eva-001`, …, starts that MAGI (`python -m magi`), and returns `name` on the create response. `group` opens a conversation with the operator only.

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

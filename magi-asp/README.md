# magi-asp

Run with `python main.py` (what the desktop app does, from this directory) or the
`magi-asp` console script.

Layout: `main.py` is the composition root and process entry (`AspServer`,
`create_app`, `main`), `server/` holds the ASP itself — `app.py` for the HTTP/WS
routes, `service.py`/`store.py`/`transport.py`/`spawn.py`/`operator.py` for the
local-network layer MAGI processes join — and `db/` owns the sqlite file.

HTTP `/conversations` (operator plus-button), `/sessions` (MAGI wire), and WebSocket `/connect`. SQLite is `~/.magi/asp/asp.sqlite`. MAGI and the desktop are clients.

magi-asp is **intranet by default**: it binds `127.0.0.1:42069`. Spawned MAGI attach with a Bearer for this origin. When a MAGI receives `session.invited`, it joins immediately — no public-network approval and no config wizard.

`POST /conversations` `{ "kind": "bot" | "group" }` does not take a MAGI name, model, or settings. `bot` is ASP-side spawn only (the desktop never starts MAGI): ASP assigns `name` `eva-000`, then `eva-001`, …, starts that MAGI (`python -m magi`), and returns `name` on the create response. `group` opens a conversation with the operator only.

Group profile **邀请** uses:

- `GET /bots` — MAGI this operator can add
- `POST /conversations/{conversation_id}/members` `{ "handle" }` — invite that MAGI; it joins on receipt

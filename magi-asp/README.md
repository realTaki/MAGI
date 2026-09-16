# magi-asp

Python package `magi_asp`. Run with `magi-asp` or `python -m magi_asp`.

HTTP `/conversations` (operator plus-button), `/sessions` (MAGI wire), and WebSocket `/connect`. SQLite is `~/.magi/asp.sqlite`. MAGI and the desktop are clients.

`POST /conversations` `{ "kind": "bot" | "group" }` does not take a name, model, or settings. `bot` registers and starts a MAGI, then opens a DM (operator + one agent). `group` opens a conversation with the operator only.

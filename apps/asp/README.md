# ASP

Casper (`@asp.magi`) hosts the HTTP/WebSocket chat relay in its launcher child.
It stores relay state under `$MAGI_HOME/asp`, including `asp.sqlite`, and uses
`AspWorker` to participate in chats like other nodes. It does not start agent
processes.

Build from the repository root, then run the ASP role through
`apps/eva/eva.mjs --role asp` with MAGI's bundled Node. Do not execute the
`dist/asp.js` class export as a server CLI. See [Running MAGI](../../docs/running.md)
for complete commands/address configuration and [Architecture](../../docs/architecture.md)
for authentication, delivery and acknowledgement behavior.

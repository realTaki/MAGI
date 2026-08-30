# @magi/asp

ASP (Agent Session Protocol) routes for the MAGI Webapp.

The Webapp mounts this package under `/asp` in its only process and its only
SQLite database, `~/.magi/app.sqlite`. It implements registration, trust
allowlists, common session HTTP operations, event replay and `WS /asp/connect`.
It is deliberately a local operator and does not yet claim ASP v0.1
conformance.

```bash
npm start --prefix webapp
```

Register local agents with `POST /asp/agents`, then use the returned Bearer
token for `/asp/sessions`, event replay and `/asp/connect`.

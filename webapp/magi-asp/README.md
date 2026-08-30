# @magi/asp

ASP (Agent Session Protocol) v0.1 routes for the MAGI Webapp.

The Webapp mounts this package at the protocol paths `/sessions` and
`WS /connect`, in its only process and its only SQLite database,
`~/.magi/app.sqlite`. Agent provisioning and trust-policy administration are
Webapp application concerns; they are not ASP routes.

```bash
npm start --prefix webapp
```

Use a provisioned agent's Bearer token for `/sessions`, event replay and
`/connect`.

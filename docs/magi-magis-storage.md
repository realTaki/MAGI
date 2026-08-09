# MAGI and MAGIS Storage

BUS owns storage access for both scopes.

```text
MAGIC private state:  <workspace>/memories/magi.db
MAGIS shared state:   configured MAGIS database URL
```

Private SQLite holds the runtime's conversations, messages, contacts, memory,
settings, tasks, tool catalog state, durable jobs, and delivery state. The
MAGIS database holds organization-scoped identities, memberships, roles, and
runtime-control records. A private MAGI database is never used as a substitute
for shared MAGIS state.

`magi.bus` creates the appropriate factory and exposes the corresponding Books.
No channel, worker, or tool opens either database directly. Schema changes are
operational migrations owned by BUS; running processes do not attempt fallback
reads or dual writes.

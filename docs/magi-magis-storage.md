# MAGI and MAGIS Storage

BUS owns storage access for both scopes.

```text
MAGI private state:   <workspace>/memories/magi.db
MAGIS shared state:   SQLite: <host>/MAGI_Societies/<magis-name>/magis.db
                      PostgreSQL: one database in the shared PG service
```

Private SQLite holds the runtime's conversations, messages, contacts, memory,
settings, tasks, tool catalog state, durable jobs, and delivery state. The
The MAGIS database holds organization-scoped identities, memberships, roles,
runtime-control records, and singleton WebUI control settings. A private MAGI
database is never used as a substitute for shared MAGIS state.

`MAGIS_NAME` is a lowercase storage slug and defaults to `genesis`. When
`MAGIS_DATABASE_URL` is absent, it selects the named SQLite file above. When a
URL is present, it is authoritative: SQLite URLs identify a per-MAGIS file and
PostgreSQL URLs identify a distinct database (for example `magis_42`) on a
shared PostgreSQL service. The BUS does not infer or silently reuse another
MAGIS's URL.

Schema materialisation is also scoped: local Books and durable job boards are
created only in the MAGI-private store; `library.magis` Books are created only
in the MAGIS store. This prevents an otherwise separate pair of DSNs from
accidentally receiving each other's tables.

MAGIS rows never carry SQL foreign keys into local tables. For example,
MAGIS-level admin and credential records retain their contact association as
an opaque identity value that the BUS/API validates; this keeps the same
schema valid for a remote PostgreSQL MAGIS and an isolated local SQLite MAGI.

`magi.bus` creates the appropriate factory and exposes the corresponding Books.
No channel, worker, or tool opens either database directly. Schema changes are
operational migrations owned by BUS; running processes do not attempt fallback
reads or dual writes.

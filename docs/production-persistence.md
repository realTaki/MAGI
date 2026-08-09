# Production Persistence

BUS owns durable runtime state. A deployed MAGIC writes private state to
`/MAGI_Citizens/<MAGI_NAME>/memories/magi.db` (or the equivalent resolved
workspace path) and reaches organization state through its configured MAGIS
database URL.

Keep private and organization storage separate, provision both through the
deployment profile, and back them up independently. Workers perform external
effects outside transactions; their durable input and terminal result remain in
BUS Job Boards for recovery.

Database changes are explicit BUS migrations. The running application uses one
schema and one implementation, without fallback reads, compatibility imports,
or dual writes.

# Bot-local native memory

The current implementation is the independent **`@magi/memory`** Host bundle.
See the [plugin contract and limits](<../packages/memory/README.md>) for its
storage, automatic context and management tools.

- Facts are persistent and isolated by Bot `DSH_HOME`, shared across that Bot's
  conversations and profiles, not across Bots.
- All Agent scopes inherit a required literal runtime context. Complete custom
  prompts and suppression of optional context do not drop the memory snapshot.
- The management tools are `list_memory`, `get_memory`, `save_memory`,
  `archive_memory`, and `delete_memory`. Updates use expected-version checks.
- Storage is a new SQLite database in `DSH_HOME/memory/memories.sqlite`, not the
  former BUS database. Installing or unloading does not migrate/delete old data.
- Archiving/deleting affects future snapshots, not prior conversation logs.
- No automatic extraction, TTL, vector search or subconscious scheduler.

The retired BUS memory package and its obsolete documentation have been deleted,
including launcher dependencies and worker registration. Existing BUS databases
and historical records are not deleted or migrated. Historical source remains in
Git history; the new plugin does not load it.

## Live verification, 2026-10-28

The native Plugin Manager installed and activated `@magi/memory@0.1.0` in the
current parent Host. A disposable verification bundle created two independent
Sessions and made three real Agent requests through `openai-codex/gpt-6.1-sol`:

1. A normal fresh conversation received the saved synthetic fact automatically.
2. A fresh conversation with a complete custom prompt and optional-context
   suppression still received the required memory snapshot.
3. The first conversation's next request received the committed updated fact.

Questions contained neither answer. All five tool schemas were visible; model
execution was guarded and no model tool calls occurred. Native tools were also
exercised for create/update/get/archive/delete; a disallowed write failed the
Agent guard. Archived facts disappeared from the next assembled memory context.
The synthetic record was then removed, both Agent handles disposed, and the
verification bundle uninstalled; the real memory plugin remains enabled.

The [verification receipt](<../../magi-maintenance/native-memory-verification/receipt.json>)
records actual request-boundary observations and durable Session evidence. The two
persisted test Session logs remain; cleanup did not erase their history. An updated
snapshot coexists with older snapshots in history, as expected.

This does not certify GUI visual rendering, live compaction, forks, Schedule or
Host restart end-to-end; those paths rely on the native context mechanism and
separate persistence/unit coverage. The running `eva-000` was not restarted or
modified. Its current process has not loaded this new plugin. EVA source and
portable package wiring are prepared; after the parent EVA manager is safely
reloaded, subsequently started Bots use the new composition. Neither that parent
reload nor deployment to running Bots has been performed.

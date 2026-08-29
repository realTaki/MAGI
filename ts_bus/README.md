# ts_bus

This is a small, structurally comparable TypeScript slice of MAGI-BUS. It
contains only the pieces needed to attach a provider plugin:

```text
Launcher
  -> Bus.forWorker(slots)
    -> BusForWorker
      -> DemoProvider.attach(...)
        -> GetSettingJobBoard -> private SettingsBook
        -> CallLLMJobBoard
```

## Layout

```text
src/
  base/
    sqlite.ts           Node 22 native SQLite connection and transactions
    job.ts              Job DTO, Slot, JobBoard contract
    baseWorker.ts       attach/detach lifecycle
  firmware/
    books/settingsBook.ts
    jobs/settingsJobs.ts
    jobs/callLLMJob.ts
  db/schema.ts          Drizzle schema source of truth
    index.ts            mounts the private Book and JobBoards
  bus.ts                owns Firmware and allocates Slots
  busForWorker.ts       identity-bound plugin surface
  index.ts              public BUS API
demo/
  provider.ts           plugin; imports only the public BUS API
  launcher.ts           opens BUS and attaches the plugin
  main.ts               runnable entry point
```

The demo provider contains no LLM SDK or inference logic. On attach it boosts
one default setting and reads it through `GetSettingJob`. It declares ownership
of `CallLLMJob.claim` and `CallLLMJob.submitResult`, showing exactly where real
provider logic would connect later. `SettingsBook` and storage are never
exposed to the plugin.

## SQLite tables

`Bus.open(workspace)` opens `<workspace>/memories/magi.db`. Firmware creates
the following tables, intentionally paralleling the Python rows:

| TypeScript table | Python counterpart | Contents |
| --- | --- | --- |
| `books_settings` | `SettingRow` | setting key/value records |
| `jobs_get_setting` | `GetSettingJobRow` | durable Settings query and result |
| `jobs_call_llm` | `CallLLMJobRow` | request payload, claimant, result, error |

The TypeScript schema is [`src/db/schema.ts`](src/db/schema.ts); its reviewed,
generated SQL history lives in [`drizzle/`](drizzle/). BUS applies pending
migrations at open and records them in `__drizzle_migrations`.
`CallLLMJobBoard` uses `BEGIN IMMEDIATE` for claim, then conditionally updates
only a claimed row owned by that provider. All public plugin values remain
plain JSON DTOs; only Firmware touches SQL.

## Schema-change workflow

1. Edit `src/db/schema.ts`.
2. Run `npm run generate:migration`; review the newly generated `drizzle/*.sql`.
3. Commit the schema file and its migration together.
4. On next `Bus.open(...)`, `base/migrations.ts` applies each unseen SQL file
   inside `BEGIN IMMEDIATE` and stores its SHA-256 in `__drizzle_migrations`.

The migration runner deliberately rejects an edited, already-applied SQL file.
During disposable local experiments you may use Drizzle's `push`, but do not
use it once a workspace has data worth preserving.

```bash
nvm use 22 # or another Node 22+ runtime
npm install
npm run generate:migration # after editing src/db/schema.ts
npm run demo
npm test
```

This package requires Node 22+ for the built-in `node:sqlite` driver. A
PostgreSQL adapter can replace `base/sqlite.ts` without changing the
provider-facing API.

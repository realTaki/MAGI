# playground

This is a small, structurally comparable TypeScript slice of MAGI-BUS. It
contains only the pieces needed to attach a provider plugin:

```text
Launcher (the composition root)
  -> Caller       [llm.call.publish]
  -> DemoProvider [settings.get.*, llm.call.claim, llm.call.submitResult]
  -> ResultReader [llm.call.getResult]

Caller -> CallLLMJobBoard -> SQLite <- CallLLMJobBoard <- DemoProvider
                                |
                           ResultReader
```

## Layout

```text
src/
  base/
    sqlite.ts           better-sqlite3 connection, transactions, and migrations
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
  modules/caller.ts     request-only module
  modules/provider.ts   execution-only module
  modules/resultReader.ts  completed-result-only module
  launcher.ts           the only composition root; opens BUS and attaches modules
  main.ts               runnable entry point
```

Each module imports only `src/index.ts`, never another module. `Caller` cannot
claim or read a call, `DemoProvider` cannot publish one, and `ResultReader`
cannot create or execute one. The launcher is deliberately the only file that
imports all three. `SettingsBook` and storage are never exposed outside
Firmware/JobBoards.

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
4. On next `Bus.open(...)`, Drizzle's `better-sqlite3` migrator applies each
   unseen SQL file and records it in `__drizzle_migrations`.

During disposable local experiments you may use Drizzle's `push`, but do not
use it once a workspace has data worth preserving.

```bash
nvm use 20 # or another Node 20+ runtime
npm install
npm run generate:migration # after editing src/db/schema.ts
npm run demo
npm test
```

This package uses `better-sqlite3`; its small adapter is the only persistence
entrypoint for Books and JobBoards. A PostgreSQL adapter can replace
`base/sqlite.ts` without changing the provider-facing API.

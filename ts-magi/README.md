# ts-magi

This is a small npm-workspaces TypeScript monorepo. BUS and each plugin are
separate packages; the launcher is a separate application:

## Development environment

Open this directory in its **Dev Container** (VS Code: “Reopen in Container”).
The container supplies its own Node 20, npm, Python, `make`, and C++ compiler;
it therefore does not use the host's Node/npm or global packages. On creation,
it runs `npm ci`, including the native `better-sqlite3` addon.

Outside the container, `.nvmrc` pins the compatible Node major version, but the
container is the supported reproducible environment.

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
packages/
  bus/                  @magi/bus
    src/                public API plus BUS-private Firmware, Books and JobBoards
    drizzle/            schema migration history
  caller/               @magi/caller: request-only worker
  provider/             @magi/provider: execution-only worker
  result-reader/        @magi/result-reader: completed-result-only worker
apps/
  launcher/             @magi/launcher: the only composition root and integration tests
```

Each plugin package declares only `@magi/bus` as a dependency, never another
plugin package. `Caller` cannot
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

The TypeScript schema is [`packages/bus/src/db/schema.ts`](packages/bus/src/db/schema.ts); its reviewed,
generated SQL history lives in [`packages/bus/drizzle/`](packages/bus/drizzle/). BUS applies pending
migrations at open and records them in `__drizzle_migrations`.
`CallLLMJobBoard` uses `BEGIN IMMEDIATE` for claim, then conditionally updates
only a claimed row owned by that provider. All public plugin values remain
plain JSON DTOs; only Firmware touches SQL.

## Schema-change workflow

1. Edit `packages/bus/src/db/schema.ts`.
2. Run `npm run generate:migration`; review the newly generated `packages/bus/drizzle/*.sql`.
3. Commit the schema file and its migration together.
4. On next `Bus.open(...)`, Drizzle's `better-sqlite3` migrator applies each
   unseen SQL file and records it in `__drizzle_migrations`.

During disposable local experiments you may use Drizzle's `push`, but do not
use it once a workspace has data worth preserving.

```bash
nvm use # fallback only; prefer the Dev Container
npm install
npm run generate:migration # after editing packages/bus/src/db/schema.ts
npm run demo
npm test
```

This package uses `better-sqlite3`; its small adapter is the only persistence
entrypoint for Books and JobBoards. A PostgreSQL adapter can replace
`base/sqlite.ts` without changing the provider-facing API.

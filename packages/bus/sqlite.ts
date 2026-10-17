/** SQLite through Node's built-in `node:sqlite`. One session, no native addon. */

import { DatabaseSync, type SQLInputValue, type StatementSync } from "node:sqlite";
import type { WithCacheConfig } from "drizzle-orm/cache/core/types";
import { entityKind } from "drizzle-orm/entity";
import { readMigrationFiles } from "drizzle-orm/migrator";
import type { TablesRelationalConfig } from "drizzle-orm/relations";
import { fillPlaceholders, type Query } from "drizzle-orm/sql";
import {
  BaseSQLiteDatabase,
  SQLitePreparedQuery,
  SQLiteSession,
  SQLiteSyncDialect,
  SQLiteTransaction,
  type SQLiteTransactionConfig,
} from "drizzle-orm/sqlite-core";
import type { SelectedFieldsOrdered } from "drizzle-orm/sqlite-core/query-builders/select.types";
import * as drizzleUtils from "drizzle-orm/utils";

const mapResultRow = (drizzleUtils as unknown as {
  mapResultRow: (
    columns: SelectedFieldsOrdered,
    row: unknown[],
    joinsNotNullableMap: Record<string, boolean> | undefined,
  ) => unknown;
}).mapResultRow;

/** What `run()` returns. BigInt row ids that fit in a safe integer are numbers. */
export type RunChanges = { changes: number; lastInsertRowid: number };

export type TransactionBehavior = "deferred" | "immediate" | "exclusive";

const depthOf = new WeakMap<DatabaseSync, number>();

export function openDatabase(path: string, options?: { readOnly?: boolean }): DatabaseSync {
  return options === undefined ? new DatabaseSync(path) : new DatabaseSync(path, options);
}

/** Run one PRAGMA and return its first column. `source` is the body, such as `foreign_keys = ON`. */
export function pragma(db: DatabaseSync, source: string): number | string | null | undefined {
  const row = db.prepare(`PRAGMA ${source}`).get();
  if (row === undefined) return undefined;
  const value = Object.values(row)[0];
  if (typeof value === "bigint") return integer(value);
  if (value === null || typeof value === "number" || typeof value === "string") return value;
  return undefined;
}

/**
 * Run `fn` inside a transaction on `db`.
 * The outermost call is `BEGIN` / `COMMIT`, and an exception runs `ROLLBACK`.
 * A call already inside a transaction uses `SAVEPOINT`, and an exception runs
 * `ROLLBACK TO SAVEPOINT` so the outer transaction stays open.
 */
export function transaction<T>(db: DatabaseSync, fn: () => T, behavior: TransactionBehavior = "deferred"): T {
  const depth = depthOf.get(db) ?? 0;
  const nested = depth > 0;
  const savepoint = `sp${depth}`;
  db.exec(nested ? `SAVEPOINT ${savepoint}` : `BEGIN ${behavior.toUpperCase()}`);
  depthOf.set(db, depth + 1);
  try {
    const result = fn();
    db.exec(nested ? `RELEASE SAVEPOINT ${savepoint}` : "COMMIT");
    return result;
  } catch (error) {
    try {
      if (nested) {
        db.exec(`ROLLBACK TO SAVEPOINT ${savepoint}`);
        db.exec(`RELEASE SAVEPOINT ${savepoint}`);
      } else {
        db.exec("ROLLBACK");
      }
    } catch {
      // The caller handles the original exception.
    }
    throw error;
  } finally {
    depthOf.set(db, depth);
  }
}

type QueryMetadata = {
  type: "select" | "update" | "delete" | "insert";
  tables: string[];
};

type ResultMapper = (rows: unknown[][], mapColumnValue?: (value: unknown) => unknown) => unknown;

class NodeSqlitePreparedQuery extends SQLitePreparedQuery<{
  type: "sync";
  run: RunChanges;
  all: unknown;
  get: unknown;
  values: unknown;
  execute: unknown;
}> {
  static readonly [entityKind] = "NodeSqlitePreparedQuery";
  /** @internal */
  joinsNotNullableMap?: Record<string, boolean>;

  constructor(
    private readonly statement: StatementSync,
    query: Query,
    queryMetadata: QueryMetadata | undefined,
    cacheConfig: WithCacheConfig | undefined,
    private readonly fields: SelectedFieldsOrdered | undefined,
    executeMethod: "run" | "all" | "get",
    private readonly customResultMapper: ResultMapper | undefined,
  ) {
    super("sync", executeMethod, query, undefined, queryMetadata, cacheConfig);
  }

  run(placeholderValues?: Record<string, unknown>): RunChanges {
    const result = this.statement.run(...this.#params(placeholderValues));
    return { changes: integer(result.changes), lastInsertRowid: integer(result.lastInsertRowid) };
  }

  all(placeholderValues?: Record<string, unknown>): unknown {
    if (!this.fields && !this.customResultMapper) {
      return this.statement.all(...this.#params(placeholderValues)).map((row) => ({ ...row }));
    }
    const rows = this.values(placeholderValues) as unknown[][];
    if (this.customResultMapper) return this.customResultMapper(rows);
    return rows.map((row) => mapResultRow(this.fields!, row, this.joinsNotNullableMap));
  }

  get(placeholderValues?: Record<string, unknown>): unknown {
    const params = this.#params(placeholderValues);
    if (!this.fields && !this.customResultMapper) {
      const row = this.statement.get(...params);
      return row === undefined ? undefined : { ...row };
    }
    const row = this.#arrayRow(params);
    if (!row) return undefined;
    if (this.customResultMapper) return this.customResultMapper([row]);
    return mapResultRow(this.fields!, row, this.joinsNotNullableMap);
  }

  values(placeholderValues?: Record<string, unknown>): unknown[] {
    return this.#arrayRows(this.#params(placeholderValues));
  }

  #params(placeholderValues?: Record<string, unknown>): SQLInputValue[] {
    return (fillPlaceholders(this.query.params, placeholderValues ?? {}) as unknown[]).map(bind);
  }

  #arrayRows(params: SQLInputValue[]): unknown[][] {
    this.statement.setReturnArrays(true);
    try {
      return this.statement.all(...params) as unknown as unknown[][];
    } finally {
      this.statement.setReturnArrays(false);
    }
  }

  #arrayRow(params: SQLInputValue[]): unknown[] | undefined {
    this.statement.setReturnArrays(true);
    try {
      return this.statement.get(...params) as unknown as unknown[] | undefined;
    } finally {
      this.statement.setReturnArrays(false);
    }
  }
}

class NodeSqliteTransaction extends SQLiteTransaction<"sync", RunChanges, Record<string, never>, TablesRelationalConfig> {
  static readonly [entityKind] = "NodeSqliteTransaction";
}

class NodeSqliteSession extends SQLiteSession<"sync", RunChanges, Record<string, never>, TablesRelationalConfig> {
  static readonly [entityKind] = "NodeSqliteSession";

  constructor(
    private readonly client: DatabaseSync,
    private readonly sqliteDialect: SQLiteSyncDialect,
  ) {
    super(sqliteDialect);
  }

  prepareQuery(
    query: Query,
    fields: SelectedFieldsOrdered | undefined,
    executeMethod: "run" | "all" | "get",
    _isResponseInArrayMode: boolean,
    customResultMapper?: ResultMapper,
    queryMetadata?: QueryMetadata,
    cacheConfig?: WithCacheConfig,
  ): NodeSqlitePreparedQuery {
    return new NodeSqlitePreparedQuery(
      this.client.prepare(query.sql),
      query,
      queryMetadata,
      cacheConfig,
      fields,
      executeMethod,
      customResultMapper,
    );
  }

  transaction<T>(
    body: (tx: SQLiteTransaction<"sync", RunChanges, Record<string, never>, TablesRelationalConfig>) => T,
    config?: SQLiteTransactionConfig,
  ): T {
    const tx = new NodeSqliteTransaction("sync", this.sqliteDialect, this, undefined);
    return transaction(this.client, () => body(tx), config?.behavior ?? "deferred");
  }
}

const opened = new WeakMap<SqliteDb, { dialect: SQLiteSyncDialect; session: NodeSqliteSession }>();

export class SqliteDb extends BaseSQLiteDatabase<"sync", RunChanges> {
  static readonly [entityKind] = "SqliteDb";
  readonly $client: DatabaseSync;

  constructor(dialect: SQLiteSyncDialect, session: NodeSqliteSession, client: DatabaseSync) {
    super("sync", dialect, session as never, undefined);
    this.$client = client;
  }
}

export function drizzle(client: DatabaseSync): SqliteDb {
  const dialect = new SQLiteSyncDialect();
  const session = new NodeSqliteSession(client, dialect);
  const db = new SqliteDb(dialect, session, client);
  opened.set(db, { dialect, session });
  return db;
}

export function migrate(db: SqliteDb, migrationsFolder: string): void {
  const parts = opened.get(db);
  if (!parts) throw new Error("SQLite database was not opened by the data layer");
  parts.dialect.migrate(
    readMigrationFiles({ migrationsFolder }),
    parts.session as never,
    { migrationsFolder },
  );
}

function integer(value: number | bigint): number {
  if (typeof value === "number") return value;
  const asNumber = Number(value);
  if (!Number.isSafeInteger(asNumber) || BigInt(asNumber) !== value) {
    throw new Error("SQLite integer does not fit in a JavaScript number");
  }
  return asNumber;
}

/** Positional binds only. A plain object would be read as named parameters. */
function bind(value: unknown): SQLInputValue {
  if (value === undefined || value === null) return null;
  if (typeof value === "string" || typeof value === "number" || typeof value === "bigint") return value;
  if (typeof value === "boolean") return value ? 1 : 0;
  if (value instanceof Uint8Array) return value;
  if (typeof Buffer !== "undefined" && Buffer.isBuffer(value)) {
    return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  }
  throw new TypeError("SQLite parameters are positional values");
}

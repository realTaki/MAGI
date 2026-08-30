/** The single SQLite connection owned by one Webapp process. */
import { chmodSync, existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { homedir } from "node:os";
import { join } from "node:path";

import initSqlJs from "sql.js";

const require = createRequire(import.meta.url);
const sqlPromise = initSqlJs({ locateFile: (file) => require.resolve(`sql.js/dist/${file}`) });

export function defaultAppDataDir() {
  return join(homedir(), ".magi");
}

export function defaultAppDatabasePath() {
  return join(defaultAppDataDir(), "app.sqlite");
}

export async function openLocalDatabase({ dataDir = defaultAppDataDir() } = {}) {
  mkdirSync(dataDir, { recursive: true, mode: 0o700 });
  if (process.platform !== "win32") chmodSync(dataDir, 0o700);
  const databasePath = join(dataDir, "app.sqlite");
  const SQL = await sqlPromise;
  const db = existsSync(databasePath) ? new SQL.Database(readFileSync(databasePath)) : new SQL.Database();

  function persist() {
    const temporary = `${databasePath}.tmp`;
    writeFileSync(temporary, db.export(), { mode: 0o600 });
    if (process.platform !== "win32") chmodSync(temporary, 0o600);
    renameSync(temporary, databasePath);
    if (process.platform !== "win32") chmodSync(databasePath, 0o600);
  }

  function transaction(action) {
    db.run("BEGIN IMMEDIATE");
    try {
      const result = action();
      db.run("COMMIT");
      persist();
      return result;
    } catch (error) {
      db.run("ROLLBACK");
      throw error;
    }
  }

  return { db, databasePath, persist, transaction, close: () => db.close() };
}

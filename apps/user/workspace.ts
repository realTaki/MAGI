import { existsSync, renameSync, chmodSync } from "node:fs";
import { join } from "node:path";

/** Resolve legacy state before BUS or any worker creates the new directory. */
export function prepareUserWorkspace(home: string): string {
  const current = join(home, ".magi", "user");
  const legacy = join(home, ".magi", "app");
  if (existsSync(legacy)) {
    if (existsSync(current)) {
      throw new Error(`Both User state directories exist; retained without merging: ${legacy} and ${current}`);
    }
    renameSync(legacy, current);
  }
  return current;
}

/** Keep provider credentials in the User database as private as the old key file. */
export function secureUserDatabases(workspace: string): void {
  for (const database of ["memories/books.db", "logs/jobs.db"]) {
    for (const suffix of ["", "-wal", "-shm"]) {
      const file = join(workspace, `${database}${suffix}`);
      if (existsSync(file)) chmodSync(file, 0o600);
    }
  }
}

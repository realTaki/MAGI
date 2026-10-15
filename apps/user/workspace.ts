import { existsSync, chmodSync } from "node:fs";
import { join } from "node:path";

export function prepareUserWorkspace(home: string): string {
  return join(home, ".magi", "user");
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

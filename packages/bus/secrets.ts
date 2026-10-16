import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

/**
 * The workspace secret store. Plaintext lives in this file and nowhere the
 * model reads: not SettingsBook, not MessageBook, not a tool result.
 */
export class SecretStore {
  private readonly values = new Map<string, string>();

  constructor(private readonly file: string) {
    if (!existsSync(file)) return;
    const parsed: unknown = JSON.parse(readFileSync(file, "utf8"));
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      throw new Error("secrets.json is not an object");
    }
    for (const [key, value] of Object.entries(parsed)) {
      if (typeof value === "string" && value !== "") this.values.set(key, value);
    }
  }

  static open(workspace: string): SecretStore {
    mkdirSync(workspace, { recursive: true });
    return new SecretStore(join(workspace, "secrets.json"));
  }

  get(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  names(): string[] {
    return [...this.values.keys()];
  }

  set(key: string, value: string): void {
    if (value === "") {
      this.values.delete(key);
    } else {
      this.values.set(key, value);
    }
    const body = `${JSON.stringify(Object.fromEntries(this.values), null, 2)}\n`;
    writeFileSync(this.file, body, { mode: 0o600 });
    try { chmodSync(this.file, 0o600); } catch { /* Windows has no POSIX mode bits. */ }
  }
}

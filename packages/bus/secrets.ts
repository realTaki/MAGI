import { closeSync, existsSync, fsyncSync, mkdirSync, openSync, readFileSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { join } from "node:path";

/**
 * The workspace secret store. Normal auth/config paths keep plaintext out of
 * SettingsBook, MessageBook and tool results. This is not an OS isolation
 * boundary: trusted same-user filesystem/shell tools can still read this file.
 */
export class SecretStore {
  private values = new Map<string, string>();

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
    const next = new Map(this.values);
    if (value === "") next.delete(key); else next.set(key, value);
    const body = `${JSON.stringify(Object.fromEntries(next), null, 2)}\n`;
    const temporary = `${this.file}.${randomUUID()}.tmp`;
    let fd: number | undefined;
    try {
      fd = openSync(temporary, "wx", 0o600);
      writeFileSync(fd, body);
      fsyncSync(fd);
      closeSync(fd); fd = undefined;
      renameSync(temporary, this.file);
      this.values = next;
    } finally {
      if (fd !== undefined) closeSync(fd);
      try { unlinkSync(temporary); } catch { /* Renamed, or creation failed. */ }
    }
  }
}

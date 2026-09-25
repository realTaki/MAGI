import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, normalize, resolve, sep } from "node:path";

export class PromptBook {
  private readonly root: string;
  /** Defaults belong to the running code, never to the operator workspace. */
  private readonly defaults = new Map<string, string>();

  constructor(workspace: string) {
    this.root = resolve(workspace, "prompts");
    mkdirSync(this.root, { recursive: true });
  }

  register(key: string, value: string): void {
    const clean = this.validateActiveKey(key);
    this.defaults.set(clean, value);
    if (!existsSync(this.path(clean))) this.write(clean, value);
  }

  get(key: string): string | null {
    const clean = this.validateActiveKey(key);
    return this.read(clean) ?? this.defaults.get(clean) ?? null;
  }

  set(key: string, value: string): void {
    this.write(key, value);
  }

  reset(key: string): boolean {
    const clean = this.validateActiveKey(key);
    const value = this.defaults.get(clean);
    if (value === undefined) return false;
    this.write(clean, value);
    return true;
  }

  private validateActiveKey(key: string): string {
    const [owner, ...rest] = this.validate(key).split("/");
    if (!owner || !rest.length || rest[0] === "defaults") throw new Error(`invalid active prompt key: ${key}`);
    return [owner, ...rest].join("/");
  }

  private path(key: string): string {
    const relative = `${this.validate(key)}.md`;
    const target = resolve(this.root, normalize(relative));
    if (target !== this.root && !target.startsWith(`${this.root}${sep}`)) throw new Error(`prompt path escapes workspace: ${key}`);
    return target;
  }

  private validate(key: string): string {
    const clean = key.trim().replace(/\.md$/, "");
    if (!clean || clean.startsWith("/") || clean.split("/").some((part) => !part || part === "." || part === "..")) throw new Error(`invalid prompt key: ${key}`);
    return clean;
  }

  private read(key: string): string | null {
    const path = this.path(key);
    return existsSync(path) ? readFileSync(path, "utf8") : null;
  }

  private write(key: string, value: string): void {
    const path = this.path(key);
    mkdirSync(dirname(path), { recursive: true });
    const temporary = `${path}.${process.pid}.tmp`;
    writeFileSync(temporary, value, "utf8");
    renameSync(temporary, path);
  }
}

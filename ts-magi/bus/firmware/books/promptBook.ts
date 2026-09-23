import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, normalize, resolve, sep } from "node:path";

export class PromptBook {
  private readonly root: string;

  constructor(workspace: string) {
    this.root = resolve(workspace, "prompts");
    mkdirSync(this.root, { recursive: true });
  }

  register(key: string, value: string): void {
    const defaultKey = this.defaultKey(key);
    this.write(defaultKey, value);
    if (!existsSync(this.path(key))) this.write(key, value);
  }

  get(key: string): string | null {
    return this.read(key) ?? this.read(this.defaultKey(key));
  }

  set(key: string, value: string): void {
    this.write(key, value);
  }

  reset(key: string): boolean {
    const value = this.read(this.defaultKey(key));
    if (value === null) return false;
    this.write(key, value);
    return true;
  }

  private defaultKey(key: string): string {
    const [owner, ...rest] = this.validate(key).split("/");
    if (!owner || !rest.length || rest[0] === "defaults") throw new Error(`invalid active prompt key: ${key}`);
    return [owner, "defaults", ...rest].join("/");
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

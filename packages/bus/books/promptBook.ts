import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, normalize, resolve, sep } from "node:path";

/** One block of system context: the heading a module's block carries, and its text. */
export type PromptSection = { readonly title: string; readonly body: string };

/** What one module's block looks like right now, for this conversation. */
export type PromptSource = (chat_id: number) => string | null;

/**
 * Where the agent's system context comes from: files an operator edits, and live
 * blocks a module answers for. A source is asked on every read — the way `ToolBook`
 * asks its own sources — so a worker that stopped is simply not in the answer.
 */
export class PromptBook {
  private readonly root: string;
  /** Defaults belong to the running code, never to the operator workspace. */
  private readonly defaults = new Map<string, string>();
  /** Live blocks, keyed by the module that owns them, in registration order. */
  private readonly sources = new Map<string, { title: string; source: PromptSource }>();

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

  /**
   * A module says where its block comes from; `sections()` asks it on every read, so
   * what it found — or no longer has — is the answer, not a copy taken once.
   */
  registerSource(owner: string, title: string, source: PromptSource): void {
    if (!owner.trim() || !title.trim()) throw new Error("a prompt source needs an owner and a title");
    this.sources.set(owner, { title, source });
  }

  /**
   * What the agent renders between its own blocks, asked in registration order. The
   * conversation is passed through for the blocks that are about one chat.
   */
  sections(chat_id: number): PromptSection[] {
    const sections: PromptSection[] = [];
    for (const { title, source } of this.sources.values()) {
      const body = source(chat_id);
      if (body) sections.push({ title, body });
    }
    return sections;
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

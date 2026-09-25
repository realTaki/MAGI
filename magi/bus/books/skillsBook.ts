import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export type Skill = { name: string; description: string };

export class SkillsBook {
  constructor(private readonly workspace: string) { this.seedDefaults(); }

  list(): Skill[] {
    const root = join(this.workspace, "skills");
    let names: string[];
    try { names = readdirSync(root); } catch { return []; }
    return names.map((name) => this.get(name)).filter((skill): skill is Skill => skill !== null);
  }

  get(name: string): Skill | null {
    if (!/^[a-zA-Z0-9_.-]{1,64}$/.test(name)) return null;
    const raw = this.read(name);
    if (raw === null) return null;
    const description = /^description:\s*["']?(.+?)["']?\s*$/m.exec(raw)?.[1]?.trim();
    return description ? { name, description: description.slice(0, 240) } : null;
  }

  read(name: string): string | null {
    if (!/^[a-zA-Z0-9_.-]{1,64}$/.test(name)) return null;
    try { return readFileSync(join(this.workspace, "skills", name, "SKILL.md"), "utf8"); }
    catch { return null; }
  }

  private seedDefaults(): void {
    const target = join(this.workspace, "skills");
    mkdirSync(target, { recursive: true });
    const source = defaultSkillsRoot();
    if (!source) return;
    for (const name of readdirSync(source)) {
      const from = join(source, name);
      const to = join(target, name);
      if (!existsSync(to) && existsSync(join(from, "SKILL.md"))) cpSync(from, to, { recursive: true });
    }
  }
}

// The checkout's ``skills/``, found by walking up from this module: the same walk
// covers a compiled copy under ``dist/`` and survives this file moving inside bus/.
function defaultSkillsRoot(): string | null {
  for (let dir = dirname(fileURLToPath(import.meta.url)); ; dir = dirname(dir)) {
    const candidate = join(dir, "skills");
    if (existsSync(candidate)) return candidate;
    if (dirname(dir) === dir) return null;
  }
}

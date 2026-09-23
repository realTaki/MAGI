import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

export type Skill = { name: string; description: string };

export class SkillsBook {
  constructor(private readonly workspace: string) {}

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
}

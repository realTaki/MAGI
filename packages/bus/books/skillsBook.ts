/**
 * The skills this MAGI has — a registry, not a reader.
 *
 * The files live where the skills package puts them, and only that package's
 * worker knows the layout: it reads them and hands the results to `replace()`.
 * Everything else (the system prompt, `load_skill`) just asks here.
 */

export type Skill = { name: string; description: string; content: string };

export class SkillsBook {
  private entries = new Map<string, Skill>();

  /** Replace everything: the worker that owns the files is the only writer. */
  replace(skills: Skill[]): void {
    this.entries = new Map(skills.map((skill) => [skill.name, skill]));
  }

  list(): Skill[] {
    return [...this.entries.values()];
  }

  get(name: string): Skill | null {
    return this.entries.get(name) ?? null;
  }

  read(name: string): string | null {
    return this.entries.get(name)?.content ?? null;
  }
}

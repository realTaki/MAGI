/**
 * Skills: instructions a MAGI pulls in only when it needs them.
 *
 * This package owns where skills live — the built-in ones that ship with the
 * project (`builtin_skills/` here) and the MAGI's own editable copies
 * (`<workspace>/skills`) — and it owns the tool that loads them. `bus.skills`
 * is only a registry: it holds what this worker found, so nothing else has to
 * know the layout, and the prompt book asks this package for the summaries the
 * system prompt lists.
 *
 * The built-in ones are copied into the workspace once, never overwritten: a
 * MAGI's copies are its own to edit, and an update must not clobber them.
 */

import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { BaseWorker, type Bus, type ExecutableTool, type Skill } from "@magi/bus";
import { skillTools } from "./tools.js";

/** A skill is a directory of that name holding a `SKILL.md` with a description. */
const NAME = /^[a-zA-Z0-9_.-]{1,64}$/;
const DESCRIPTION = /^description:\s*["']?(.+?)["']?\s*$/m;

export class SkillWorker extends BaseWorker {
  readonly worker_name = "skills";
  private readonly own: ExecutableTool[];
  private started = false;

  constructor(bus: Bus) {
    super(bus);
    this.own = skillTools(bus);
    // Offered only while this worker runs: a stopped worker would leave the model
    // holding a tool whose files nobody has read.
    bus.tools.registerSource("skills", () => (this.started ? this.own : []));
    // The system prompt lists the summaries; this package is asked for them, so the
    // agent never reads the registry — or needs to know this worker exists.
    bus.prompts.registerSource("skills", "Available skills", () => catalog(bus.skills.list()));
  }

  async start(): Promise<void> {
    if (this.started) return;
    this.started = true;
    const root = join(this.bus.workspace, "skills");
    const shipped = shippedSkills();
    if (shipped) seedWorkspace(root, shipped);
    this.bus.skills.replace(loadSkills(root));
  }

  async poll(): Promise<boolean> {
    const board = this.bus.board("RunToolJob");
    const job = board.claim(this.worker_name, (input) => this.own.some((tool) => tool.name === input.call.name));
    if (!job) return false;
    const tool = this.own.find((candidate) => candidate.name === job.input.call.name);
    try {
      if (!tool) throw new Error(`unknown tool ${job.input.call.name}`);
      board.submit(this.worker_name, job.id, { output: { content: await tool.run(job.input.call.arguments) } });
    } catch (error) {
      board.submit(this.worker_name, job.id, { error: error instanceof Error ? error.message : String(error) });
    }
    return true;
  }

  async stop(): Promise<void> {
    // A stopped worker can be started again, so the files are re-read next time.
    this.started = false;
    this.bus.skills.replace([]);
  }
}

function loadSkills(root: string): Skill[] {
  let names: string[];
  try {
    names = readdirSync(root);
  } catch {
    return [];
  }
  return names.map((name) => parseSkill(root, name)).filter((skill): skill is Skill => skill !== null);
}

/** The block the system prompt shows: names and descriptions; `load_skill` pulls the rest. */
function catalog(skills: Skill[]): string {
  return skills.map((skill) => `- ${skill.name}: ${skill.description}`).join("\n");
}

function parseSkill(root: string, name: string): Skill | null {
  if (!NAME.test(name)) return null;
  let raw: string;
  try {
    raw = readFileSync(join(root, name, "SKILL.md"), "utf8");
  } catch {
    return null;
  }
  const description = DESCRIPTION.exec(raw)?.[1]?.trim();
  return description ? { name, description: description.slice(0, 240), content: raw } : null;
}

function seedWorkspace(root: string, shipped: string): void {
  mkdirSync(root, { recursive: true });
  for (const name of readdirSync(shipped)) {
    const from = join(shipped, name);
    const to = join(root, name);
    if (!existsSync(to) && existsSync(join(from, "SKILL.md"))) cpSync(from, to, { recursive: true });
  }
}

/**
 * This package's own `builtin_skills/`, found by walking up from this module:
 * the same walk covers a compiled copy under `dist/` and survives this file
 * moving.
 */
function shippedSkills(): string | null {
  for (let dir = dirname(fileURLToPath(import.meta.url)); ; dir = dirname(dir)) {
    const candidate = join(dir, "builtin_skills");
    if (existsSync(candidate)) return candidate;
    if (dirname(dir) === dir) return null;
  }
}

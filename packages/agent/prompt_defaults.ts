import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/** Markdown templates live beside this module so code-owned prompts are discoverable. */
function template(name: string): string {
  return readPrompt(`template_${name}.md`);
}

function source(name: string): string {
  return readPrompt(`${name}.md`);
}

function readPrompt(name: string): string {
  const here = dirname(fileURLToPath(import.meta.url));
  for (const candidate of [join(here, name), join(here, "..", "..", "agent", name)]) {
    if (existsSync(candidate)) return readFileSync(candidate, "utf8");
  }
  throw new Error(`prompt template ${name} is missing`);
}

export const AGENT_PROMPT = template("AGENT");
/** Code-owned constraints: never seeded into or overridden by a workspace. */
export const SYSTEM_PROMPT = source("system");
export const COMPACTION_PROMPT = template("compaction");
/**
 * Appended to whatever compaction prompt the workspace has. An older or edited
 * copy must not be able to drop source-change outcomes on the floor.
 */
export const COMPACTION_INVARIANT = [
  "Preserve source-change outcomes that are in the history: paths, what was decided,",
  "whether a check passed or failed, and any rebuild that was requested, with its target.",
  "Do not copy tool transcripts, file bodies, or line-numbered reads.",
].join(" ");

export const PROMPT_DEFAULTS: ReadonlyArray<readonly [string, string]> = [
  ["agent/AGENT", AGENT_PROMPT],
  ["agent/compaction", COMPACTION_PROMPT],
];

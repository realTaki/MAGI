import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

/** Markdown templates live beside this module so code-owned prompts are discoverable. */
function template(name: string): string {
  return readFileSync(fileURLToPath(new URL(`./template_${name}.md`, import.meta.url)), "utf8");
}

function source(name: string): string {
  return readFileSync(fileURLToPath(new URL(`./${name}.md`, import.meta.url)), "utf8");
}

export const AGENT_PROMPT = template("AGENT");
/** Code-owned constraints: never seeded into or overridden by a workspace. */
export const SYSTEM_PROMPT = source("system");
export const COMPACTION_PROMPT = template("compaction");

export const PROMPT_DEFAULTS: ReadonlyArray<readonly [string, string]> = [
  ["agent/AGENT", AGENT_PROMPT],
  ["agent/compaction", COMPACTION_PROMPT],
];

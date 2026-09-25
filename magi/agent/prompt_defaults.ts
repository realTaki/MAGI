import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

/** Markdown templates live beside this module so code-owned prompts are discoverable. */
function template(name: string): string {
  return readFileSync(fileURLToPath(new URL(`./template_${name}.md`, import.meta.url)), "utf8");
}

export const AGENT_PROMPT = template("AGENT");
/** This is appended at runtime, so a workspace's custom AGENT.md cannot drop it. */
export const REPLY_FORMAT_PROMPT = template("reply_format");
export const COMPACTION_PROMPT = template("compaction");
export const SKILLS_BLOCK_PROMPT = template("skills_block");

export const PROMPT_DEFAULTS: ReadonlyArray<readonly [string, string]> = [
  ["agent/AGENT", AGENT_PROMPT],
  ["agent/compaction", COMPACTION_PROMPT],
  ["agent/skills_block", SKILLS_BLOCK_PROMPT],
];

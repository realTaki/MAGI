import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Magi } from "../eva.js";
import { VERIFY_BUILD_ARGS, VERIFY_TEST_ARGS, VERIFY_UI_ARGS } from "@magi/runtime/verify.js";
import { expect, test } from "./test.js";

const skillPath = resolve(dirname(fileURLToPath(import.meta.url)), "../../../../packages/skills/builtin_skills/self_update/SKILL.md");

test("self_update skill teaches the same compile and test commands as verifyCheckout", () => {
  const skill = readFileSync(skillPath, "utf8");
  assert.match(skill, /^name: self_update$/m);
  assert.match(skill, /Read and edit this node's own source, compile and run the full test suite in its own checkout, then request to replace itself after everything passes/);
  assert.match(skill, new RegExp(`"\\$MAGI_NODE" "\\$MAGI_NPM" ${VERIFY_TEST_ARGS.join(" ")}`));
  assert.match(skill, new RegExp(`"\\$MAGI_NODE" "\\$MAGI_NPM" ${VERIFY_BUILD_ARGS.join(" ")}`));
  assert.match(skill, new RegExp(VERIFY_UI_ARGS.join(" ").replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.match(skill, /request_replace/);
  assert.doesNotMatch(skill, /do not run the whole suite/i);
  assert.doesNotMatch(skill, /magi_rsi|request_rebuild|replace_self/);
  assert.doesNotMatch(skill, /^name: project$/m);
});

test("seeded skills include self_update and not project or magi_rsi", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-self-update-"));
  const magi = new Magi("@self-update.magi", { workspace, client: { async complete() { return { role: "assistant", content: "unused" }; } } });
  try {
    await magi.start();
    const names = magi.bus.skills.list().map((skill) => skill.name);
    expect(names).toContain("self_update");
    expect(names).not.toContain("project");
    expect(names).not.toContain("magi_rsi");
    expect(magi.bus.tools.catalog().map((tool) => tool.name)).toContain("request_replace");
    expect(magi.bus.tools.catalog().map((tool) => tool.name)).not.toContain("request_rebuild");
    expect(magi.bus.tools.catalog().map((tool) => tool.name)).not.toContain("replace_self");
    assert.match(magi.bus.skills.get("self_update")!.description, /compile and run the full test suite/);
  } finally {
    await magi.stop();
    await rm(workspace, { recursive: true, force: true });
  }
});

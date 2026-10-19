import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Magi } from "../eva.js";
import { VERIFY_NPM_ARGS } from "@magi/runtime/verify.js";
import { expect, test } from "./test.js";

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "../../../..");
const skillPath = resolve(here, "../../../../packages/skills/builtin_skills/self_update/SKILL.md");
const verifySource = readFileSync(resolve(repo, "packages/runtime/verify.ts"), "utf8");
const pkg = JSON.parse(readFileSync(resolve(repo, "package.json"), "utf8")) as { scripts: Record<string, string> };

test("self_update skill and verifyCheckout call the same verify entry", () => {
  const skill = readFileSync(skillPath, "utf8");
  assert.equal(pkg.scripts.verify, "node scripts/verify.mjs");
  assert.deepEqual([...VERIFY_NPM_ARGS], ["run", "verify"]);
  assert.match(skill, /^name: self_update$/m);
  assert.match(skill, /Read and edit this node's own source, compile and run the full test suite in its own checkout, then request to replace itself after everything passes/);
  assert.match(skill, new RegExp(`"\\$MAGI_NODE" "\\$MAGI_NPM" ${VERIFY_NPM_ARGS.join(" ")}`));
  assert.match(skill, /& \$env:MAGI_NODE \$env:MAGI_NPM run verify/);
  assert.match(skill, /run_in_background/);
  assert.match(skill, /bash_output/);
  assert.match(verifySource, /VERIFY_NPM_ARGS/);
  assert.match(verifySource, /execFile\(node, \[npm, \.\.\.VERIFY_NPM_ARGS\]/);
  assert.doesNotMatch(verifySource, /VERIFY_TEST_ARGS|VERIFY_BUILD_ARGS|VERIFY_UI_ARGS/);
  assert.doesNotMatch(skill, /do not run the whole suite/i);
  assert.doesNotMatch(skill, /magi_rsi|request_rebuild|replace_self/);
  assert.doesNotMatch(skill, /^name: project$/m);
  assert.doesNotMatch(skill, /target: self|target `self`/);
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
    const replace = magi.bus.tools.catalog().find((tool) => tool.name === "request_replace");
    assert.doesNotMatch(JSON.stringify(replace?.input_schema ?? {}), /"target"/);
  } finally {
    await magi.stop();
    await rm(workspace, { recursive: true, force: true });
  }
});

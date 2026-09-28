import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { COMPACTION_INVARIANT, SYSTEM_PROMPT } from "@magi/agent/prompt_defaults.js";
import { sourceCheckout } from "@magi/agent/checkout.js";
import { test } from "./test.js";

test("the system prompt says how to change the system and does not map the repository", () => {
  assert.match(SYSTEM_PROMPT, /NO_REPLY/);
  assert.match(SYSTEM_PROMPT, /load_skill/);
  assert.match(SYSTEM_PROMPT, /request_rebuild/);
  assert.match(SYSTEM_PROMPT, /does not fit in a prompt/);
  assert.doesNotMatch(SYSTEM_PROMPT, /apps\/user/);
  assert.match(COMPACTION_INVARIANT, /rebuild/);
  assert.match(COMPACTION_INVARIANT, /tool transcripts/);
});

test("source checkout is this process's tree or an explicit refusal", () => {
  const workspace = mkdtempSync(join(tmpdir(), "magi-checkout-"));
  try {
    assert.match(sourceCheckout("@eva-000.magi", workspace), /checkout: none/);
    mkdirSync(join(workspace, "MAGI", ".git"), { recursive: true });
    const present = sourceCheckout("@eva-000.magi", workspace);
    assert.match(present, /checkout: MAGI\//);
    assert.match(present, /branch: magi\/eva-000/);
    assert.doesNotMatch(present, /apps\/asp/);
  } finally {
    rmSync(workspace, { recursive: true, force: true });
  }
});

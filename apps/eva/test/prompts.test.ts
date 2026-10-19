import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { COMPACTION_INVARIANT, SYSTEM_PROMPT } from "@magi/agent/prompt_defaults.js";
import { sourceCheckout } from "@magi/agent/checkout.js";
import { ASP_MAGI_HANDLE, EVA_MAGI_HANDLE, magiLocalName, SHELL_MAGI_HANDLE } from "@magi/bus";
import { test } from "./test.js";

test("the system prompt says how to change the system and does not map the repository", () => {
  assert.match(SYSTEM_PROMPT, /NO_REPLY/);
  assert.match(SYSTEM_PROMPT, /load_skill/);
  assert.match(SYSTEM_PROMPT, /self_update/);
  assert.match(SYSTEM_PROMPT, /does not fit in a prompt/);
  assert.doesNotMatch(SYSTEM_PROMPT, /apps\/user/);
  assert.doesNotMatch(SYSTEM_PROMPT, /request_rebuild|replace_self|request_replace/);
  assert.match(COMPACTION_INVARIANT, /replace/);
  assert.match(COMPACTION_INVARIANT, /tool transcripts/);
});

test("source checkout is this process's tree or an explicit refusal", () => {
  const workspace = mkdtempSync(join(tmpdir(), "magi-checkout-"));
  try {
    assert.match(sourceCheckout(EVA_MAGI_HANDLE, workspace), /checkout: none/);
    mkdirSync(join(workspace, "MAGI", ".git"), { recursive: true });
    const present = sourceCheckout(EVA_MAGI_HANDLE, workspace, "agent");
    assert.match(present, /checkout: MAGI\//);
    assert.match(present, new RegExp(`branch: magi/${magiLocalName(EVA_MAGI_HANDLE)}`));
    assert.doesNotMatch(present, /apps\/asp/);
    assert.match(sourceCheckout(EVA_MAGI_HANDLE, workspace, "shell"), /branch: magi\/shell/);
    assert.match(sourceCheckout(SHELL_MAGI_HANDLE, workspace, "shell"), new RegExp(`handle: ${SHELL_MAGI_HANDLE.replace(".", "\\.")}`));
    assert.match(sourceCheckout(SHELL_MAGI_HANDLE, workspace, "shell"), /branch: magi\/shell/);
    assert.match(sourceCheckout(ASP_MAGI_HANDLE, workspace, "asp"), /branch: magi\/asp/);
  } finally {
    rmSync(workspace, { recursive: true, force: true });
  }
});

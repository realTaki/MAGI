import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Magi } from "../eva.js";
import { agentWorkers } from "@magi/launcher/composition/roles.js";
import { createOwnerApi } from "@magi/launcher/composition/owner.js";
import { resolveToolchain } from "@magi/launcher/runtime/toolchain.js";

test("production modules omit CLI while the local owner adapter delivers replies", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-modules-"));
  const magi = new Magi("@modules.magi", { workspace, owner: true, peer: { port: 0 }, client: {
    async complete() { return { role: "assistant", content: "owner reply" }; },
  } });
  try {
    const names = agentWorkers(magi.bus, { owner: true }).map(worker => worker.worker_name);
    assert.ok(names.includes("peer"), "A2A retains the persisted peer identity");
    assert.ok(names.includes("local-owner"));
    assert.ok(!names.includes("cli"));
    assert.ok(!("chat" in magi), "terminal chat API is removed from production");
    const owner = createOwnerApi(magi.bus);
    await magi.start();
    owner["owner.chat"]({ content: "hello" });
    const deadline = Date.now() + 8_000;
    while (!owner["owner.events"]().some(message => message.content === "owner reply")) {
      assert.ok(Date.now() < deadline, "owner reply timed out");
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    assert.ok(resolveToolchain().node.includes("runtime"), "merged utilities resolve bundled Node");
  } finally { await magi.stop(); await rm(workspace, { recursive: true, force: true }); }
});

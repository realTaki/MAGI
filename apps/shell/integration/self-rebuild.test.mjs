/**
 * The launcher and the child are separate processes. A revision that does not
 * become ready is killed, the running child stays up, and the failure is written
 * where the child can fail the replace job.
 */

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

const repo = path.resolve(import.meta.dirname, "../../..");
const launcher = path.join(repo, "apps", "eva", "eva.mjs");

test("a failed revision switch keeps the running child and records the marker", async (t) => {
  const home = mkdtempSync(path.join(tmpdir(), "magi-launcher-"));
  const child = path.join(home, "child.mjs");
  const kept = path.join(home, "kept");
  writeFileSync(child, `import { writeFileSync } from "node:fs";
if (process.env.MAGI_REVISION === "bad") process.exit(2);
process.send({ type: "ready" });
setTimeout(() => {
  process.send({ v: 1, type: "switch", revision: "bad", previous: "good", checkout: ${JSON.stringify(home)}, job: 7 });
}, 100);
process.on("message", (message) => {
  if (message?.type === "switch-failed") writeFileSync(${JSON.stringify(kept)}, message.error ?? "");
  if (message?.type === "shutdown") process.exit(0);
});
setInterval(() => {}, 1000);
`);
  const proc = spawn(process.execPath, [launcher, "--role", "agent", "@eva-002.magi"], {
    env: { ...process.env, MAGI_HOME: home, MAGI_CHILD: child, MAGI_NODE: process.execPath },
    stdio: ["ignore", "pipe", "pipe", "ipc"],
    detached: true,
  });
  let stderr = "";
  proc.stderr?.on("data", (chunk) => { stderr += String(chunk); });
  t.after(() => {
    try { process.kill(-proc.pid, "SIGTERM"); } catch { /* already gone */ }
    rmSync(home, { recursive: true, force: true });
  });
  const marker = path.join(home, "eva-002", "replace-failed.json");
  const deadline = Date.now() + 8_000;
  while (Date.now() < deadline && proc.exitCode == null) {
    try {
      const body = JSON.parse(readFileSync(marker, "utf8"));
      const note = readFileSync(kept, "utf8");
      assert.equal(body.job, 7);
      assert.match(body.error, /exited 2/);
      assert.match(note, /exited 2/);
      assert.equal(proc.exitCode, null);
      return;
    } catch (error) {
      if (error && error.code !== "ENOENT") throw error;
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  assert.fail(`launcher did not record the failed switch (${proc.exitCode}): ${stderr}`);
});

/**
 * The launcher runs a revision's own child, backs off when that child crashes,
 * and hands the host pipes to a new launcher.
 */

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { createSupervisor } from "../process.mjs";

const repo = path.resolve(import.meta.dirname, "../../..");
const launcher = path.join(repo, "apps", "eva", "eva.mjs");

function childAt(home, name, revision, source) {
  const file = path.join(home, name, "revisions", revision, "packages", "node", "dist", "child.js");
  mkdirSync(path.dirname(file), { recursive: true });
  writeFileSync(file, source);
  return path.dirname(path.dirname(path.dirname(path.dirname(file))));
}

function start(home, args, env, t) {
  const proc = spawn(process.execPath, [launcher, ...args], {
    env: { ...process.env, MAGI_HOME: home, MAGI_NODE: process.execPath, ...env },
    stdio: ["pipe", "pipe", "pipe", "ipc"],
    detached: true,
  });
  let stderr = "";
  proc.stderr?.on("data", (chunk) => { stderr += String(chunk); });
  t.after(() => {
    try { process.kill(-proc.pid, "SIGTERM"); } catch { /* already gone */ }
  });
  return { proc, stderr: () => stderr };
}

test("a switch runs the new revision's child", async (t) => {
  const home = mkdtempSync(path.join(tmpdir(), "magi-switch-"));
  const proof = path.join(home, "proof");
  const dirB = childAt(home, "eva-002", "b", `process.send({ v: 1, type: "hello" });
process.send({ v: 1, type: "ready" });
require("node:fs").writeFileSync(${JSON.stringify(proof)}, "from-b");
process.on("message", (message) => { if (message?.type === "shutdown") process.exit(0); });
setInterval(() => {}, 1000);
`);
  childAt(home, "eva-002", "a", `process.send({ v: 1, type: "hello" });
process.on("message", (message) => { if (message?.type === "shutdown") process.exit(0); });
process.send({ v: 1, type: "ready" });
setTimeout(() => process.send({ v: 1, type: "switch", revision: "b", dir: ${JSON.stringify(dirB)}, job: 3 }), 30);
setInterval(() => {}, 1000);
`);
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const { proc, stderr } = start(home, ["--role", "agent", "@eva-002.magi"], { MAGI_REVISION_DIR: path.join(home, "eva-002", "revisions", "a"), MAGI_REVISION: "a" }, t);
  const deadline = Date.now() + 8_000;
  while (Date.now() < deadline && proc.exitCode == null) {
    try {
      assert.equal(readFileSync(proof, "utf8"), "from-b");
      const state = JSON.parse(readFileSync(path.join(home, "eva-002", "launcher.json"), "utf8"));
      assert.equal(state.current, "b");
      assert.equal(state.previous, "a");
      return;
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
    await new Promise((resolve) => setTimeout(resolve, 30));
  }
  assert.fail(`new revision did not run (${proc.exitCode}): ${stderr()}`);
});

test("a crashing child is restarted with backoff and then reported", async (t) => {
  const home = mkdtempSync(path.join(tmpdir(), "magi-crash-"));
  const log = path.join(home, "starts");
  const child = path.join(home, "child.mjs");
  writeFileSync(child, `import { appendFileSync } from "node:fs";
appendFileSync(${JSON.stringify(log)}, String(Date.now()) + "\\n");
process.send({ v: 1, type: "hello" });
process.send({ v: 1, type: "ready" });
setTimeout(() => process.exit(1), 20);
`);
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const { proc, stderr } = start(home, ["--role", "agent", "@eva-002.magi"], {
    MAGI_CHILD: child,
    MAGI_CRASH_LIMIT: "3",
    MAGI_CRASH_BACKOFF_MS: "40",
  }, t);
  const code = await new Promise((resolve) => proc.once("exit", resolve));
  const starts = readFileSync(log, "utf8").trim().split("\n").map(Number);
  assert.equal(starts.length, 4);
  assert.ok(starts[1] - starts[0] >= 35, "first restart waits");
  assert.ok(starts[2] - starts[1] >= 70, "second restart waits longer");
  assert.ok(starts[3] - starts[2] >= 140, "third restart waits longer still");
  const marker = JSON.parse(readFileSync(path.join(home, "eva-002", "replace-failed.json"), "utf8"));
  assert.match(marker.error, /crashed 4 times/);
  assert.match(stderr(), /crashed 4 times/);
  assert.equal(code, 1);
});

test("a failed switch starts the previous revision again", async (t) => {
  const home = mkdtempSync(path.join(tmpdir(), "magi-rollback-"));
  const runs = path.join(home, "runs");
  const restored = path.join(home, "restored");
  const bad = childAt(home, "eva-002", "bad", "process.exit(2);\n");
  childAt(home, "eva-002", "a", `const { readFileSync, writeFileSync, existsSync } = require("node:fs");
const n = existsSync(${JSON.stringify(runs)}) ? Number(readFileSync(${JSON.stringify(runs)}, "utf8")) : 0;
writeFileSync(${JSON.stringify(runs)}, String(n + 1));
process.send({ v: 1, type: "hello" });
process.on("message", (message) => { if (message?.type === "shutdown") process.exit(0); });
process.send({ v: 1, type: "ready" });
if (n === 0) setTimeout(() => process.send({ v: 1, type: "switch", revision: "bad", dir: ${JSON.stringify(bad)}, job: 8 }), 30);
else writeFileSync(${JSON.stringify(restored)}, "previous");
setInterval(() => {}, 1000);
`);
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const { proc, stderr } = start(home, ["--role", "agent", "@eva-002.magi"], {
    MAGI_REVISION_DIR: path.join(home, "eva-002", "revisions", "a"),
    MAGI_REVISION: "a",
  }, t);
  const deadline = Date.now() + 8_000;
  while (Date.now() < deadline && proc.exitCode == null) {
    try {
      assert.equal(readFileSync(restored, "utf8"), "previous");
      const marker = JSON.parse(readFileSync(path.join(home, "eva-002", "replace-failed.json"), "utf8"));
      assert.equal(marker.job, 8);
      assert.match(marker.error, /exited 2/);
      return;
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
    await new Promise((resolve) => setTimeout(resolve, 30));
  }
  assert.fail(`previous revision did not return (${proc.exitCode}): ${stderr()}`);
});

test("a hosted upgrade finishes the job after the old launcher exits", async (t) => {
  const home = mkdtempSync(path.join(tmpdir(), "magi-hosted-"));
  const oldPid = path.join(home, "old.pid");
  const done = path.join(home, "done");
  const dir = childAt(home, "eva-002", "next", `const { writeFileSync } = require("node:fs");
process.send({ v: 1, type: "hello" });
process.send({ v: 1, type: "ready" });
if (process.env.MAGI_REPLACE_JOB) writeFileSync(${JSON.stringify(done)}, process.env.MAGI_REPLACE_JOB);
process.on("message", (message) => { if (message?.type === "shutdown") process.exit(0); });
setInterval(() => {}, 1000);
`);
  childAt(home, "eva-002", "old", `const { writeFileSync } = require("node:fs");
writeFileSync(${JSON.stringify(oldPid)}, String(process.ppid));
process.send({ v: 1, type: "hello" });
process.on("message", (message) => { if (message?.type === "shutdown") process.exit(0); });
process.send({ v: 1, type: "ready" });
setTimeout(() => process.send({ v: 1, type: "upgrade", revision: "next", dir: ${JSON.stringify(dir)}, launcher: ${JSON.stringify(launcher)}, job: 9 }), 30);
setInterval(() => {}, 1000);
`);
  const supervisor = createSupervisor({ node: process.execPath, env: { ...process.env, MAGI_NODE: process.execPath } });
  t.after(async () => {
    await supervisor.close();
    rmSync(home, { recursive: true, force: true });
  });
  const handle = await supervisor.start({
    entry: launcher,
    cwd: path.dirname(launcher),
    args: ["--role", "agent", "@eva-002.magi"],
    env: {
      MAGI_HOME: home,
      MAGI_HOST: "1",
      MAGI_NODE: process.execPath,
      MAGI_REVISION_DIR: path.join(home, "eva-002", "revisions", "old"),
      MAGI_REVISION: "old",
    },
  }, { host: {} });
  await handle.whenReady();
  const deadline = Date.now() + 8_000;
  while (Date.now() < deadline) {
    try {
      if (readFileSync(done, "utf8") === "9") break;
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
    await new Promise((resolve) => setTimeout(resolve, 30));
  }
  assert.equal(readFileSync(done, "utf8"), "9");
  const pid = Number(readFileSync(oldPid, "utf8"));
  assert.throws(() => process.kill(pid, 0), /ESRCH/);
});

function exists(file) {
  try { readFileSync(file); return true; } catch { return false; }
}

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

test("a launcher upgrade stops the old child before the new launcher reads the host pipe", async (t) => {
  const home = mkdtempSync(path.join(tmpdir(), "magi-upgrade-"));
  const down = path.join(home, "down");
  const piped = path.join(home, "piped");
  const nextPid = path.join(home, "next.pid");
  const next = path.join(home, "next-launcher.mjs");
  const dir = childAt(home, "eva-002", "next", `process.send({ v: 1, type: "hello" });
process.send({ v: 1, type: "ready" });
process.on("message", (message) => { if (message?.type === "shutdown") process.exit(0); });
setInterval(() => {}, 1000);
`);
  writeFileSync(next, `import { writeFileSync, existsSync } from "node:fs";
import { spawn } from "node:child_process";
if (!existsSync(${JSON.stringify(down)})) process.exit(2);
writeFileSync(${JSON.stringify(nextPid)}, String(process.pid));
const text = await new Promise((resolve) => {
  process.stdin.once("data", (chunk) => resolve(String(chunk)));
  setTimeout(() => resolve(""), 2000);
});
writeFileSync(${JSON.stringify(piped)}, text);
const child = spawn(process.execPath, [${JSON.stringify(path.join(dir, "packages", "node", "dist", "child.js"))}, "--role", "agent"], { stdio: ["pipe", "pipe", "pipe", "ipc"] });
process.stdin.pipe(child.stdin);
child.on("message", (message) => {
  if (message?.type === "ready") process.send({ v: 1, type: "ready" });
});
`);
  childAt(home, "eva-002", "old", `const { writeFileSync } = require("node:fs");
process.send({ v: 1, type: "hello" });
process.on("message", (message) => {
  if (message?.type === "shutdown") { writeFileSync(${JSON.stringify(down)}, "1"); process.exit(0); }
});
process.send({ v: 1, type: "ready" });
setTimeout(() => process.send({ v: 1, type: "upgrade", revision: "next", dir: ${JSON.stringify(dir)}, launcher: ${JSON.stringify(next)}, job: 4 }), 30);
setInterval(() => {}, 1000);
`);
  t.after(() => {
    try { process.kill(Number(readFileSync(nextPid, "utf8")), "SIGTERM"); } catch { /* not started */ }
    rmSync(home, { recursive: true, force: true });
  });
  const { proc, stderr } = start(home, ["--role", "agent", "@eva-002.magi"], {
    MAGI_HOST: "1",
    MAGI_REVISION_DIR: path.join(home, "eva-002", "revisions", "old"),
    MAGI_REVISION: "old",
  }, t);
  const deadline = Date.now() + 8_000;
  while (Date.now() < deadline && !exists(nextPid)) await new Promise((resolve) => setTimeout(resolve, 20));
  proc.stdin.write("piped\n");
  const code = await new Promise((resolve) => proc.once("exit", (exit) => resolve(exit)));
  assert.equal(readFileSync(down, "utf8"), "1");
  assert.equal(readFileSync(piped, "utf8"), "piped\n");
  assert.equal(code, 0, stderr());
});

function exists(file) {
  try { readFileSync(file); return true; } catch { return false; }
}

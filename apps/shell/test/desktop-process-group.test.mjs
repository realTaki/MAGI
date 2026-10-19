import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createProcessOwner } from "../process-owner.mjs";

const linuxTest = process.platform === "linux" ? test : test.skip;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function group(pid = process.pid) { const raw = readFileSync(`/proc/${pid}/stat`, "utf8"); return Number(raw.slice(raw.lastIndexOf(")") + 2).split(" ")[2]); }
function live(pid) { try { const raw = readFileSync(`/proc/${pid}/stat`, "utf8"); return !raw.slice(raw.lastIndexOf(")") + 2).startsWith("Z"); } catch (error) { if (error.code === "ENOENT") return false; throw error; } }
async function until(check) { const deadline = Date.now() + 5000; while (!check()) { assert.ok(Date.now() < deadline, "process condition timed out"); await sleep(20); } }

linuxTest("ordinary process ownership still creates its own detached group", async (t) => {
  const owner = createProcessOwner({ graceMs: 20 }); t.after(() => owner.close());
  const child = owner.spawn(process.execPath, ["-e", "setInterval(()=>{},1000)"], { stdio: "ignore", detached: false });
  await until(() => live(child.pid));
  assert.equal(group(child.pid), child.pid); assert.notEqual(group(child.pid), group());
  await owner.close(); await until(() => !live(child.pid));
});

linuxTest("candidate shared-group owner kills only its own root and descendants", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-shared-group-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const marker = path.join(root, "child.pid"), script = path.join(root, "tree.mjs");
  writeFileSync(script, `import{spawn}from'node:child_process';import{writeFileSync}from'node:fs';const child=spawn(process.execPath,['-e',"process.on('SIGTERM',()=>{});setInterval(()=>{},1000)"],{stdio:'ignore'});writeFileSync(${JSON.stringify(marker)},String(child.pid));process.on('SIGTERM',()=>{});setInterval(()=>{},1000);`);
  const sibling = spawn(process.execPath, ["-e", "setInterval(()=>{},1000)"], { stdio: "ignore" });
  const siblingExit = new Promise((resolve) => sibling.once("exit", resolve));
  t.after(async () => { sibling.kill("SIGKILL"); await siblingExit; });
  const owner = createProcessOwner({ keepProcessGroup: true, graceMs: 20 }); t.after(() => owner.close());
  const child = owner.spawn(process.execPath, [script], { stdio: "ignore" });
  await until(() => { try { return Number(readFileSync(marker, "utf8")) > 0; } catch { return false; } });
  const descendant = Number(readFileSync(marker, "utf8"));
  assert.equal(group(child.pid), group()); assert.equal(group(descendant), group()); assert.equal(group(sibling.pid), group());
  await owner.close(); await until(() => !live(child.pid) && !live(descendant));
  assert.equal(live(sibling.pid), true, "unrelated sibling in the same group was signalled");
});

linuxTest("Eva ignores a desktop group marker inherited into a different process group", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-mismatched-group-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const backend = path.join(root, "backend.mjs"), marker = path.join(root, "backend.pid");
  const control = pathToFileURL(fileURLToPath(new URL("../../../packages/node/dist/control.js", import.meta.url))).href;
  writeFileSync(backend, `import{writeFileSync}from'node:fs';import{sendLauncher,untilShutdown}from ${JSON.stringify(control)};sendLauncher({type:'hello'});writeFileSync(${JSON.stringify(marker)},String(process.pid));const shutdown=untilShutdown();sendLauncher({type:'ready'});await shutdown;process.exit(0);`);
  const owner = createProcessOwner({ graceMs: 50 }); t.after(() => owner.close());
  const launcher = owner.spawn(process.execPath, [fileURLToPath(new URL("../../eva/eva.mjs", import.meta.url)), "--role", "agent"], {
    stdio: ["ignore", "ignore", "ignore", "ipc"],
    env: { ...process.env, MAGI_HOME: path.join(root, "home"), MAGI_HOST: "0", MAGI_CHILD: backend, MAGI_NODE: process.execPath, MAGI_DESKTOP_PROCESS_GROUP: String(group()) },
  });
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Eva did not become ready")), 5000);
    launcher.on("message", (message) => { if (message?.type === "ready") { clearTimeout(timer); resolve(); } });
    launcher.once("exit", (code) => { clearTimeout(timer); reject(new Error(`Eva exited ${code}`)); });
  });
  const pid = Number(readFileSync(marker, "utf8"));
  assert.equal(group(launcher.pid), launcher.pid); assert.equal(group(pid), pid);
  assert.notEqual(group(pid), group(launcher.pid));
  await owner.close(); await until(() => !live(launcher.pid) && !live(pid));
});

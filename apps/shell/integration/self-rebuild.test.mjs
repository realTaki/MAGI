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
import { pathToFileURL } from "node:url";
import { Bus, EVA_MAGI_HANDLE, magiLocalName, messageDelivery } from "@magi/bus";
import { createSupervisor } from "../process.mjs";
import { applyInterfaceUpdate } from "../interface.mjs";

const repo = path.resolve(import.meta.dirname, "../../..");
const launcher = path.join(repo, "apps", "eva", "eva.mjs");
const node = magiLocalName(EVA_MAGI_HANDLE);

function childAt(home, name, revision, source) {
  const file = path.join(home, name, "revisions", revision, "packages", "launcher", "dist", "composition", "child.js");
  mkdirSync(path.dirname(file), { recursive: true });
  writeFileSync(file, source);
  return path.join(home, name, "revisions", revision);
}

function start(home, args, env, t) {
  const proc = spawn(process.execPath, [launcher, ...args], {
    env: { ...process.env, MAGI_HOME: home, MAGI_NODE: process.execPath, ...env },
    stdio: ["pipe", "pipe", "pipe", "ipc"],
    detached: process.platform !== "win32",
  });
  let stderr = "";
  proc.stderr?.on("data", (chunk) => { stderr += String(chunk); });
  t.after(async () => {
    // Keep the fixtures' real launcher/child trees owned on both platforms.
    // POSIX negative PIDs do not terminate a Windows tree and leave pipes open.
    if (proc.exitCode === null && proc.signalCode === null) {
      let timer;
      const closed = new Promise((resolve) => proc.once("close", resolve));
      try {
        await Promise.race([(async () => {
          if (process.platform === "win32") {
            await new Promise((resolve, reject) => {
              const killer = spawn("taskkill.exe", ["/pid", String(proc.pid), "/t", "/f"], { stdio: "ignore", windowsHide: true });
              killer.once("error", reject);
              killer.once("exit", (code) => code === 0 || proc.exitCode !== null || proc.signalCode !== null
                ? resolve() : reject(new Error(`taskkill fixture launcher exited ${code}`)));
            });
          } else {
            try { process.kill(-proc.pid, "SIGTERM"); }
            catch (error) { if (error.code !== "ESRCH") throw error; }
          }
          await closed;
        })(), new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error(`fixture launcher ${proc.pid} did not exit`)), 5000);
        })]);
      } finally { clearTimeout(timer); }
    }
    rmSync(home, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  });
  return { proc, stderr: () => stderr };
}

test("a switch runs the new revision's child", async (t) => {
  const home = mkdtempSync(path.join(tmpdir(), "magi-switch-"));
  const proof = path.join(home, "proof");
  const dirB = path.join(home, node, "revisions", "b");
  const childB = path.join(dirB, "packages", "launcher", "dist", "composition", "child.js");
  childAt(home, node, "a", `const { mkdirSync, writeFileSync } = require("node:fs");
const { dirname } = require("node:path");
process.send({ v: 1, type: "hello" });
process.on("message", (message) => { if (message?.type === "shutdown") process.exit(0); });
process.send({ v: 1, type: "ready" });
setTimeout(() => {
  const file = ${JSON.stringify(childB)};
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, ${JSON.stringify(`process.send({ v: 1, type: "hello" });\nprocess.send({ v: 1, type: "ready" });\nrequire("node:fs").writeFileSync(${JSON.stringify(proof)}, "from-b");\nprocess.on("message", (message) => { if (message?.type === "shutdown") process.exit(0); });\nsetInterval(() => {}, 1000);\n`)});
  process.send({ v: 1, type: "switch", revision: "b", dir: ${JSON.stringify(dirB)}, job: 3 });
}, 80);
setInterval(() => {}, 1000);
`);
  const { proc, stderr } = start(home, ["--role", "agent", EVA_MAGI_HANDLE], { MAGI_REVISION_DIR: path.join(home, node, "revisions", "a"), MAGI_REVISION: "a" }, t);
  const deadline = Date.now() + 8_000;
  while (Date.now() < deadline && proc.exitCode == null) {
    try {
      // The new child can write the proof before the launcher records current=b.
      // Keep waiting for both; only ENOENT used to retry, so `'a' !== 'b'` aborted the test.
      if (readFileSync(proof, "utf8") !== "from-b") {
        await new Promise((resolve) => setTimeout(resolve, 30));
        continue;
      }
      const state = JSON.parse(readFileSync(path.join(home, node, "launcher.json"), "utf8"));
      if (state.current === "b" && state.previous === "a") return;
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
  const { proc, stderr } = start(home, ["--role", "agent", EVA_MAGI_HANDLE], {
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
  const marker = JSON.parse(readFileSync(path.join(home, node, "replace-failed.json"), "utf8"));
  assert.match(marker.error, /crashed 4 times/);
  assert.match(stderr(), /crashed 4 times/);
  assert.equal(code, 1);
});

test("a failed switch starts the previous revision again", async (t) => {
  const home = mkdtempSync(path.join(tmpdir(), "magi-rollback-"));
  const runs = path.join(home, "runs");
  const restored = path.join(home, "restored");
  const bad = path.join(home, node, "revisions", "bad");
  childAt(home, node, "a", `const { mkdirSync, readFileSync, writeFileSync, existsSync } = require("node:fs");
const { dirname } = require("node:path");
const n = existsSync(${JSON.stringify(runs)}) ? Number(readFileSync(${JSON.stringify(runs)}, "utf8")) : 0;
writeFileSync(${JSON.stringify(runs)}, String(n + 1));
process.send({ v: 1, type: "hello" });
process.on("message", (message) => { if (message?.type === "shutdown") process.exit(0); });
process.send({ v: 1, type: "ready" });
if (n === 0) setTimeout(() => {
  const file = ${JSON.stringify(path.join(bad, "packages", "launcher", "dist", "composition", "child.js"))};
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, "process.exit(2);\\n");
  process.send({ v: 1, type: "switch", revision: "bad", dir: ${JSON.stringify(bad)}, job: 8 });
}, 80);
else writeFileSync(${JSON.stringify(restored)}, "previous");
setInterval(() => {}, 1000);
`);
  const { proc, stderr } = start(home, ["--role", "agent", EVA_MAGI_HANDLE], {
    MAGI_REVISION_DIR: path.join(home, node, "revisions", "a"),
    MAGI_REVISION: "a",
  }, t);
  const deadline = Date.now() + 8_000;
  while (Date.now() < deadline && proc.exitCode == null) {
    try {
      assert.equal(readFileSync(restored, "utf8"), "previous");
      const marker = JSON.parse(readFileSync(path.join(home, node, "replace-failed.json"), "utf8"));
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
  const dir = path.join(home, node, "revisions", "next");
  const nextChild = path.join(dir, "packages", "launcher", "dist", "composition", "child.js");
  childAt(home, node, "old", `const { mkdirSync, writeFileSync } = require("node:fs");
const { dirname } = require("node:path");
writeFileSync(${JSON.stringify(oldPid)}, String(process.ppid));
process.send({ v: 1, type: "hello" });
process.on("message", (message) => { if (message?.type === "shutdown") process.exit(0); });
process.send({ v: 1, type: "ready" });
setTimeout(() => {
  const file = ${JSON.stringify(nextChild)};
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, ${JSON.stringify(`const { writeFileSync } = require("node:fs");\nprocess.send({ v: 1, type: "hello" });\nprocess.send({ v: 1, type: "ready" });\nif (process.env.MAGI_REPLACE_JOB) writeFileSync(${JSON.stringify(done)}, process.env.MAGI_REPLACE_JOB);\nprocess.on("message", (message) => { if (message?.type === "shutdown") process.exit(0); });\nsetInterval(() => {}, 1000);\n`)});
  process.send({ v: 1, type: "upgrade", revision: "next", dir: ${JSON.stringify(dir)}, launcher: ${JSON.stringify(launcher)}, job: 9 });
}, 80);
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
    args: ["--role", "agent", EVA_MAGI_HANDLE],
    env: {
      MAGI_HOME: home,
      MAGI_HOST: "1",
      MAGI_NODE: process.execPath,
      MAGI_REVISION_DIR: path.join(home, node, "revisions", "old"),
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
  const stateDeadline = Date.now() + 4_000;
  let state = {};
  while (Date.now() < stateDeadline) {
    try { state = JSON.parse(readFileSync(path.join(home, node, "launcher.json"), "utf8")); }
    catch { state = {}; }
    if (state.current === "next" && state.previous === "old") break;
    await new Promise((resolve) => setTimeout(resolve, 30));
  }
  assert.equal(state.current, "next");
  assert.equal(state.previous, "old");
  assert.equal(state.currentDir, dir);
});

test("a failed switch records the switch error on the job", async (t) => {
  const home = mkdtempSync(path.join(tmpdir(), "magi-job-"));
  const previous = process.env.MAGI_HOME;
  process.env.MAGI_HOME = home;
  const bus = new Bus(EVA_MAGI_HANDLE);
  const chat = bus.chats.forChannel("cli", "operator");
  messageDelivery.setHomeChat(bus, chat.id);
  const id = bus.board("ReplaceSelfJob").publish({ swap: true }, "test");
  bus.settings.set("replace.pending", String(id));
  assert.ok(bus.board("ReplaceSelfJob").claimId("replace", id));
  bus.close();
  if (previous === undefined) delete process.env.MAGI_HOME;
  else process.env.MAGI_HOME = previous;
  const resultFile = path.join(home, "result.json");
  const badDir = path.join(home, node, "revisions", "bad");
  const busModule = pathToFileURL(path.join(repo, "packages/bus/dist/index.js")).href;
  const replaceModule = pathToFileURL(path.join(repo, "packages/launcher/dist/replace.js")).href;
  childAt(home, node, "a", `const { mkdirSync, writeFileSync } = require("node:fs");
const { dirname } = require("node:path");
async function main() {
  process.on("message", (message) => { if (message?.type === "shutdown") process.exit(0); });
  process.send({ v: 1, type: "hello" });
  try {
    if (process.env.MAGI_REPLACE_ERROR) {
      const jobId = Number(process.env.MAGI_REPLACE_JOB);
      const failure = process.env.MAGI_REPLACE_ERROR;
      const { Bus } = await import(${JSON.stringify(busModule)});
      const { ReplaceWorker } = await import(${JSON.stringify(replaceModule)});
      const bus = new Bus(${JSON.stringify(EVA_MAGI_HANDLE)});
      const worker = new ReplaceWorker(bus, { app: "eva", script: "build" }, { healthy: () => null, async suspendForReplace() {}, async resume() {} });
      await worker.poll();
      writeFileSync(${JSON.stringify(resultFile)}, JSON.stringify({ job: jobId, error: failure, result: bus.board("ReplaceSelfJob").result(jobId) }));
      bus.close();
    } else {
      setTimeout(() => {
        const badChild = ${JSON.stringify(path.join(badDir, "packages/launcher/dist/composition/child.js"))};
        mkdirSync(dirname(badChild), { recursive: true });
        writeFileSync(badChild, "process.exit(2);\\n");
        process.send({ v: 1, type: "switch", revision: "bad", dir: ${JSON.stringify(badDir)}, job: ${id} });
      }, 80);
    }
  } catch (error) {
    writeFileSync(${JSON.stringify(resultFile)}, JSON.stringify({ failed: String(error && error.stack || error) }));
  }
  process.send({ v: 1, type: "ready" });
  setInterval(() => {}, 1000);
}
main();
`);
  const { proc, stderr } = start(home, ["--role", "agent", EVA_MAGI_HANDLE], {
    MAGI_REVISION_DIR: path.join(home, node, "revisions", "a"),
    MAGI_REVISION: "a",
  }, t);
  const deadline = Date.now() + 8_000;
  let detail = "no result file";
  while (Date.now() < deadline && proc.exitCode == null) {
    try {
      detail = readFileSync(resultFile, "utf8");
      const body = JSON.parse(detail);
      if (body.failed) throw new Error(body.failed);
      const result = body.result;
      assert.equal(result.status, "failed");
      assert.match(result.error, /exited 2/);
      assert.equal(result.error.includes("crashed"), false);
      return;
    } catch (error) {
      if (error?.code !== "ENOENT") {
        writeFileSync(path.join(home, "job-result-debug.txt"), `${detail}\n${error && error.stack || error}\n${stderr()}`);
        throw error;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 30));
  }
  writeFileSync(path.join(home, "job-result-debug.txt"), `timeout ${detail}\n${stderr()}`);
  assert.fail(`job result was not recorded (${proc.exitCode}): ${stderr()}`);
});

function exists(file) {
  try { readFileSync(file); return true; } catch { return false; }
}

test('a Shell switch activates its revision UI path and auto-reloads the open window', async (t) => {
  const home = mkdtempSync(path.join(tmpdir(), 'magi-shell-ui-switch-'));
  const proof = path.join(home, 'proof.json');
  const source = path.join(home, 'shell', 'MAGI');
  const next = path.join(home, 'shell', 'revisions', 'next');
  mkdirSync(source, { recursive: true });
  childAt(home, 'shell', 'next', `require('node:fs').writeFileSync(${JSON.stringify(proof)},JSON.stringify({checkout:process.env.MAGI_CHECKOUT,appCheckout:process.env.MAGI_APP_CHECKOUT}));import(${JSON.stringify(pathToFileURL(path.join(repo,'packages/launcher/dist/composition/child.js')).href)});`);
  const candidateChild = readFileSync(path.join(next, 'packages/launcher/dist/composition/child.js'), 'utf8');
  childAt(home, 'shell', 'old', `process.send({v:1,type:'hello'});process.send({v:1,type:'ready'});process.on('message',m=>{if(m.type==='shutdown')process.exit(0)});setTimeout(()=>{const f=require('node:fs');f.mkdirSync(${JSON.stringify(path.join(next,'packages/launcher/dist/composition'))},{recursive:true});f.writeFileSync(${JSON.stringify(path.join(next,'packages/launcher/dist/composition/child.js'))},${JSON.stringify(candidateChild)});f.mkdirSync(${JSON.stringify(path.join(next,'apps/shell/dist'))},{recursive:true});f.writeFileSync(${JSON.stringify(path.join(next,'apps/shell/dist/index.html'))},'candidate UI');process.send({v:1,type:'switch',revision:'next',dir:${JSON.stringify(next)},job:7})},100);setInterval(()=>{},1000);`);
  mkdirSync(path.join(next, 'apps/shell/dist'), { recursive: true });
  writeFileSync(path.join(next, 'apps/shell/dist/index.html'), 'candidate UI');
  const events = [];
  const loaded = [];
  const win = { isDestroyed: () => false, loadFile: async (file) => { loaded.push(file); assert.equal(readFileSync(file, 'utf8'), 'candidate UI'); } };
  const supervisor = createSupervisor({ node: process.execPath, env: { ...process.env, MAGI_NODE: process.execPath } });
  t.after(async () => { await supervisor.close(); rmSync(home, { recursive: true, force: true }); });
  const handle = await supervisor.start({ entry: launcher, cwd: path.dirname(launcher), args: ['--role', 'shell'], env: { MAGI_HOME: home, MAGI_HOST: '1', MAGI_REVISION: 'old', MAGI_REVISION_DIR: path.join(home, 'shell/revisions/old'), MAGI_CHECKOUT: source, MAGI_APP_CHECKOUT: source, MAGI_NODE: process.execPath, MAGI_GIT: 'git', MAGI_NPM: path.join(repo,'apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js'), MAGI_OS_HOME: home, MAGI_USER_DATA: path.join(home,'shell/electron'), MAGI_REPOSITORY: 'https://github.com/realTaki/MAGI.git' } }, { host: { emit: (event) => { events.push(event); if (event.event === 'app.interface-updated') void applyInterfaceUpdate(handle, win, event.payload); } } });
  await handle.whenReady();
  const deadline = Date.now() + 8000;
  while (!exists(proof) && Date.now() < deadline) await new Promise(r => setTimeout(r, 20));
  const paths = JSON.parse(readFileSync(proof, 'utf8'));
  assert.equal(paths.checkout, source, 'editable Git authority stays at its original checkout');
  assert.equal(paths.appCheckout, next, 'active UI must come from the new revision');
  while ((!events.length || !loaded.length) && Date.now() < deadline) await new Promise(r => setTimeout(r, 20));
  const ui = path.join(next, 'apps/shell/dist/index.html');
  assert.deepEqual(events, [{ event: 'app.interface-updated', payload: { ui } }]);
  assert.deepEqual(loaded, [ui], 'the open window loadFiles the revision UI without confirmation');
  assert.equal(JSON.parse(readFileSync(path.join(home, 'shell/launcher.json'), 'utf8')).current, 'next');
});

test('a failed Shell candidate restores the old checkout and never offers UI activation', async (t) => {
  const home = mkdtempSync(path.join(tmpdir(), 'magi-shell-ui-failed-'));
  const proof = path.join(home, 'restored.json');
  const source = path.join(home, 'shell/MAGI'), old = path.join(home, 'shell/revisions/old'), bad = path.join(home, 'shell/revisions/bad');
  mkdirSync(source, { recursive: true });
  childAt(home, 'shell', 'old', `process.send({v:1,type:'hello'});process.on('message',m=>{if(m.type==='shutdown')process.exit(0)});process.send({v:1,type:'ready'});if(process.env.MAGI_REPLACE_ERROR)require('node:fs').writeFileSync(${JSON.stringify(proof)},JSON.stringify({checkout:process.env.MAGI_CHECKOUT,appCheckout:process.env.MAGI_APP_CHECKOUT,error:process.env.MAGI_REPLACE_ERROR}));else setTimeout(()=>{const f=require('node:fs');f.mkdirSync(${JSON.stringify(path.join(bad,'packages/launcher/dist/composition'))},{recursive:true});f.writeFileSync(${JSON.stringify(path.join(bad,'packages/launcher/dist/composition/child.js'))},"process.send({v:1,type:'hello'});process.exit(2)");process.send({v:1,type:'switch',revision:'bad',dir:${JSON.stringify(bad)},job:8})},100);setInterval(()=>{},1000);`);
  const events = [];
  const loaded = [];
  const win = { isDestroyed: () => false, loadFile: async (file) => { loaded.push(file); } };
  const supervisor = createSupervisor({ node: process.execPath, env: { ...process.env, MAGI_NODE: process.execPath } });
  t.after(async () => { await supervisor.close(); rmSync(home, { recursive: true, force: true }); });
  const handle = await supervisor.start({ entry: launcher, cwd: path.dirname(launcher), args: ['--role','shell'], env: { MAGI_HOME: home, MAGI_HOST: '1', MAGI_REVISION: 'old', MAGI_REVISION_DIR: old, MAGI_CHECKOUT: source, MAGI_APP_CHECKOUT: source, MAGI_NODE: process.execPath, MAGI_GIT: 'git', MAGI_NPM: path.join(repo,'apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js'), MAGI_OS_HOME: home, MAGI_USER_DATA: path.join(home,'shell/electron'), MAGI_REPOSITORY: 'https://github.com/realTaki/MAGI.git' } }, { host: { emit: (event) => { events.push(event); if (event.event === 'app.interface-updated') void applyInterfaceUpdate(handle, win, event.payload); } } });
  await handle.whenReady();
  const deadline = Date.now() + 8000;
  while (!exists(proof) && Date.now() < deadline) await new Promise(r => setTimeout(r,20));
  const restored = JSON.parse(readFileSync(proof,'utf8'));
  assert.equal(restored.checkout,source); assert.equal(restored.appCheckout,old); assert.match(restored.error,/exited 2/);
  const state = JSON.parse(readFileSync(path.join(home,'shell/launcher.json'),'utf8'));
  assert.equal(state.current,'old'); assert.equal(state.currentDir,old);
  assert.deepEqual(events,[]);
  assert.deepEqual(loaded, []);
});

test('desktop handoff preserves rollback revisions and consumes its override before a later node switch', async t => {
  const home = mkdtempSync(path.join(tmpdir(), 'magi-desktop-launcher-'));
  // start() owns teardown: stop the launcher tree before removing its live files.
  const trigger = path.join(home, 'switch'), proof = path.join(home, 'proof');
  const old = childAt(home, 'shell', 'old', '');
  const older = childAt(home, 'shell', 'older', '');
  const next = childAt(home, 'shell', 'next', `process.send({v:1,type:'hello'});process.send({v:1,type:'ready'});require('node:fs').writeFileSync(${JSON.stringify(proof)},JSON.stringify({journal:process.env.MAGI_DESKTOP_UPDATE_JOURNAL,revision:process.env.MAGI_REVISION,dir:process.env.MAGI_REVISION_DIR}));process.on('message',m=>{if(m?.type==='shutdown')process.exit(0)});setInterval(()=>{},1000);`);
  const candidate = childAt(home, 'shell', 'candidate', `process.send({v:1,type:'hello'});process.send({v:1,type:'ready'});const timer=setInterval(()=>{if(require('node:fs').existsSync(${JSON.stringify(trigger)})){clearInterval(timer);process.send({v:1,type:'switch',revision:'next',dir:${JSON.stringify(next)},job:10})}},20);process.on('message',m=>{if(m?.type==='shutdown')process.exit(0)});`);
  writeFileSync(path.join(home, 'shell', 'launcher.json'), JSON.stringify({ protocol:1, current:'old',currentDir:old,previous:'older',previousDir:older }));
  const {proc, stderr} = start(home, ['--role','shell'], {MAGI_REVISION:'candidate',MAGI_REVISION_DIR:candidate,MAGI_REPLACE_JOB:'9',MAGI_DESKTOP_UPDATE_JOURNAL:path.join(home,'journal.json')}, t);
  const wait = async predicate => { const deadline=Date.now()+6000;while(Date.now()<deadline){try{if(predicate())return}catch{}await new Promise(r=>setTimeout(r,20))}assert.fail(stderr())};
  await wait(()=>JSON.parse(readFileSync(path.join(home,'shell','launcher.json'),'utf8')).current==='candidate');
  assert.doesNotThrow(()=>readFileSync(path.join(older,'packages/launcher/dist/composition/child.js')),'desktop readiness must preserve the previous rollback history');
  writeFileSync(trigger,'go');
  await wait(()=>{const body=JSON.parse(readFileSync(proof,'utf8'));return body.revision==='next'});
  const body=JSON.parse(readFileSync(proof,'utf8'));
  assert.equal(body.journal,undefined);assert.equal(body.dir,undefined);assert.equal(proc.exitCode,null);
});

test('supervisor consumes desktop-only environment before restarting the same Shell', async t => {
  const home = mkdtempSync(path.join(tmpdir(),'magi-desktop-supervisor-'));
  const proof = path.join(home,'starts');
  const candidate = childAt(home,'shell','candidate',`process.send({v:1,type:'hello'});require('node:fs').appendFileSync(${JSON.stringify(proof)},JSON.stringify({job:process.env.MAGI_REPLACE_JOB,journal:process.env.MAGI_DESKTOP_UPDATE_JOURNAL,revision:process.env.MAGI_REVISION})+'\\n');process.send({v:1,type:'ready'});process.on('message',m=>{if(m?.type==='shutdown')process.exit(0)});setInterval(()=>{},1000);`);
  const supervisor = createSupervisor({node:process.execPath,env:process.env});
  t.after(async()=>{await supervisor.close();rmSync(home,{recursive:true,force:true})});
  const journal = path.join(home,'journal');
  const handle = await supervisor.start({entry:launcher,cwd:path.dirname(launcher),args:['--role','shell'],env:{MAGI_HOME:home,MAGI_NODE:process.execPath,MAGI_REVISION:'candidate',MAGI_REVISION_DIR:candidate,MAGI_REPLACE_JOB:'17',MAGI_DESKTOP_UPDATE_JOURNAL:journal}});
  await handle.whenReady();
  supervisor.consumeDesktopHandoff(journal);
  await supervisor.restart(handle.id);
  const starts=readFileSync(proof,'utf8').trim().split('\n').map(JSON.parse);
  assert.equal(starts.length,2);assert.equal(starts[0].job,'17');assert.equal(starts[1].job,undefined);assert.equal(starts[1].journal,undefined);assert.equal(starts[1].revision,'candidate');
});

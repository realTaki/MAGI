import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import {
  chmodSync, copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync,
  renameSync, rmSync, statSync, symlinkSync, writeFileSync, linkSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import {
  acknowledgeDesktopUpdate, desktopUpdateLaunchContext, pendingDesktopUpdate,
  prepareDesktopUpdate, prepareDesktopUpdateRecovery, runDesktopUpdate,
} from "../desktop-update.mjs";

const linuxTest = process.platform === "linux" ? test : test.skip;
const MODULE = fileURLToPath(new URL("../desktop-update.mjs", import.meta.url));
const OWNER_MODULE = fileURLToPath(new URL("../process-owner.mjs", import.meta.url));
const EVA_MODULE = fileURLToPath(new URL("../../eva/eva.mjs", import.meta.url));
const CONTROL_MODULE = fileURLToPath(new URL("../../../packages/node/dist/control.js", import.meta.url));
const REVISION = "a".repeat(40), PREVIOUS = "b".repeat(40);
const hash = (file) => createHash("sha256").update(readFileSync(file)).digest("hex");
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const readJSON = (file) => JSON.parse(readFileSync(file, "utf8"));
async function until(check, message, timeout = 15_000) {
  const end = Date.now() + timeout;
  while (!await check()) { if (Date.now() > end) throw new Error(message); await sleep(20); }
}
function killGroup(pid) { if (pid) { try { process.kill(-pid, "SIGKILL"); } catch (error) { if (error.code !== "ESRCH") throw error; } } }
function processChild(file, args, options = {}) {
  const child = spawn(file, args, { stdio: ["ignore", "pipe", "pipe"], ...options });
  let output = "";
  child.stdout?.on("data", (data) => { output += data; }); child.stderr?.on("data", (data) => { output += data; });
  const exited = new Promise((resolve, reject) => { child.once("error", reject); child.once("exit", (code, signal) => resolve({ code, signal, output })); });
  return { child, exited };
}
function fixture(t, { behavior = "ack", timeoutMs = 10_000, stateExists = true } = {}) {
  const root = mkdtempSync(path.join(tmpdir(), "magi's desktop transaction "));
  const home = path.join(root, "profile"), source = path.join(home, "shell", "revisions", REVISION);
  const output = path.join(source, "desktop-artifacts", "build"), state = path.join(home, "shell", "launcher.json");
  mkdirSync(output, { recursive: true });
  const target = path.join(root, "MAGI's outer.AppImage"), inner = path.join(root, "Extracted Electron");
  const installer = path.join(output, "New MAGI.AppImage"), marker = path.join(root, "launches.jsonl"), script = path.join(root, "app.mjs");
  writeFileSync(inner, "inner binary remains untouched"); chmodSync(inner, 0o755);
  const priorState = JSON.stringify({ protocol: 1, current: PREVIOUS, previous: "c".repeat(40), currentDir: "/prior/revision", untouched: "state must be byte-identical\n" }, null, 2);
  if (stateExists) { writeFileSync(state, priorState); chmodSync(state, 0o640); }
  const backend = path.join(root, "owned-backend.mjs");
  writeFileSync(backend, `import{writeFileSync,readFileSync}from'node:fs';import{sendLauncher,untilShutdown}from ${JSON.stringify(pathToFileURL(CONTROL_MODULE).href)};
const raw=readFileSync('/proc/self/stat','utf8');const group=Number(raw.slice(raw.lastIndexOf(')')+2).split(' ')[2]);
writeFileSync(${JSON.stringify(path.join(root, "owned-backend.json"))},JSON.stringify({pid:process.pid,group,marker:process.env.MAGI_DESKTOP_PROCESS_GROUP,journal:process.env.MAGI_DESKTOP_UPDATE_JOURNAL,role:process.env.MAGI_ROLE}));
sendLauncher({type:'hello'});const shutdown=untilShutdown();sendLauncher({type:'ready'});await shutdown;process.exit(0);`);
  writeFileSync(script, `import{appendFileSync,readFileSync,writeFileSync,existsSync}from'node:fs';import path from'node:path';import{spawn}from'node:child_process';
import{acknowledgeDesktopUpdate,desktopUpdateLaunchContext}from ${JSON.stringify(pathToFileURL(MODULE).href)};
import{createProcessOwner}from ${JSON.stringify(pathToFileURL(OWNER_MODULE).href)};
const mode=process.env.MAGI_DESKTOP_UPDATE_MODE;
appendFileSync(${JSON.stringify(marker)},JSON.stringify({mode,pid:process.pid,extract:process.env.APPIMAGE_EXTRACT_AND_RUN,revision:process.env.MAGI_REVISION,revisionDir:process.env.MAGI_REVISION_DIR,error:process.env.MAGI_REPLACE_ERROR,job:process.env.MAGI_REPLACE_JOB,context:desktopUpdateLaunchContext(),state:(()=>{try{return readFileSync(${JSON.stringify(state)},'utf8')}catch{return null}})()})+'\\n');
if(mode==='candidate'){
writeFileSync(${JSON.stringify(state)},'candidate modified launcher state');
const behavior=${JSON.stringify(behavior)};
if(behavior==='exit')process.exit(7);
if(behavior==='wrong-ack'){const j=JSON.parse(readFileSync(process.env.MAGI_DESKTOP_UPDATE_JOURNAL,'utf8'));writeFileSync(path.join(path.dirname(process.env.MAGI_DESKTOP_UPDATE_JOURNAL),'ready.json'),JSON.stringify({schema:1,transactionId:j.transactionId,sha256:'0'.repeat(64),revision:j.manifest.revision}));}
if(behavior==='ack')acknowledgeDesktopUpdate({revision:${JSON.stringify(REVISION)}});
if(behavior==='ack-exit')setInterval(()=>{if(existsSync(${JSON.stringify(path.join(root, 'ack-now'))})){acknowledgeDesktopUpdate({revision:${JSON.stringify(REVISION)}});process.exit(0)}},10);
if(behavior==='delayed-ack')setTimeout(()=>acknowledgeDesktopUpdate({revision:${JSON.stringify(REVISION)}}),1000);
if(behavior==='detached-descendant'){const child=spawn(process.execPath,['-e',"process.on('SIGTERM',()=>{});setInterval(()=>{},1000)"],{detached:true,stdio:'ignore'});writeFileSync(${JSON.stringify(path.join(root, "descendant.pid"))},String(child.pid));child.unref();}
if(behavior.startsWith('owned-eva-')){
const role=behavior.slice('owned-eva-'.length),owner=createProcessOwner({keepProcessGroup:desktopUpdateLaunchContext()?.rollback===false}),env={...process.env};
for(const key of Object.keys(env))if(key.startsWith('MAGI_DESKTOP_UPDATE_')||['MAGI_REVISION','MAGI_REVISION_DIR','MAGI_REPLACE_JOB','MAGI_REPLACE_ERROR'].includes(key))delete env[key];
Object.assign(env,{MAGI_CHILD:${JSON.stringify(backend)},MAGI_NODE:process.execPath,MAGI_HOST:'0',MAGI_HOME:${JSON.stringify(path.join(root, "owned-profile"))}});
const child=owner.spawn(process.execPath,[${JSON.stringify(EVA_MODULE)},'--role',role],{env,stdio:['ignore','ignore','ignore','ipc']});
child.on('message',message=>{if(message?.type==='ready'){const raw=readFileSync('/proc/'+child.pid+'/stat','utf8');writeFileSync(${JSON.stringify(path.join(root, "owned-launcher.json"))},JSON.stringify({pid:child.pid,group:Number(raw.slice(raw.lastIndexOf(')')+2).split(' ')[2])}));process.exit(7)}});
}
}
setInterval(()=>{},1000);
`);
  const quote = (value) => `'${value.replaceAll("'", "'\\''")}'`;
  writeFileSync(target, `#!/bin/sh\n# old image\nexec ${quote(process.execPath)} ${quote(script)}\n`); chmodSync(target, 0o755);
  writeFileSync(installer, behavior === "spawn-error" ? "#!/definitely/missing/appimage-interpreter\n" : `#!/bin/sh\n# candidate image\nexec ${quote(process.execPath)} ${quote(script)}\n`);
  const original = readFileSync(target), manifest = { schema: 1, revision: REVISION, previous: PREVIOUS, dir: source, installer, sha256: hash(installer), platform: "linux", arch: process.arch };
  const transactions = [], children = [];
  const prepare = async (overrides = {}) => {
    const tx = await prepareDesktopUpdate({ installer, target, execPath: inner, manifest, job: 42, pid: 2147483647, home, nodePath: process.execPath, timeoutMs, exitTimeoutMs: 3000, ...overrides });
    transactions.push(tx); return tx;
  };
  const start = (tx) => { const child = processChild(tx.nodePath, [tx.helperPath, "--run", tx.journalPath], { detached: true }); children.push(child); return child; };
  const launches = () => existsSync(marker) ? readFileSync(marker, "utf8").trim().split("\n").filter(Boolean).map(JSON.parse) : [];
  t.after(async () => {
    for (const tx of transactions) {
      if (!existsSync(tx.journalPath)) continue;
      const journal = readJSON(tx.journalPath);
      killGroup(journal.candidateProcess?.pid); killGroup(journal.rollbackProcess?.pid);
    }
    for (const child of children) { if (child.child.exitCode === null && child.child.signalCode === null) killGroup(child.child.pid); await child.exited; }
    rmSync(root, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
  });
  return { root, home, source, state, target, inner, installer, marker, original, priorState, manifest, prepare, start, launches };
}
function candidateEnv(tx, fixture) {
  return { MAGI_DESKTOP_UPDATE_JOURNAL: tx.journalPath, MAGI_DESKTOP_UPDATE_ID: tx.transactionId, MAGI_DESKTOP_UPDATE_SHA256: fixture.manifest.sha256, MAGI_DESKTOP_UPDATE_REVISION: REVISION, MAGI_DESKTOP_UPDATE_MODE: "candidate", APPIMAGE: fixture.target };
}

linuxTest("preparation records immutable identity and rejects malformed manifests, paths, hashes, and symlinks", { skip: process.platform !== "linux" }, async (t) => {
  const f = fixture(t);
  for (const patch of [{ schema: 2 }, { platform: "darwin" }, { arch: "other" }, { revision: "../escape" }, { sha256: "0".repeat(64) }, { dir: f.root }]) {
    await assert.rejects(f.prepare({ manifest: { ...f.manifest, ...patch } }));
    assert.deepEqual(readFileSync(f.target), f.original);
  }
  await assert.rejects(f.prepare({ target: f.inner }), /outer AppImage/i);
  await assert.rejects(f.prepare({ target: f.root }), /regular file/);
  const link = path.join(f.source, "linked.AppImage"); symlinkSync(f.installer, link);
  await assert.rejects(f.prepare({ installer: link, manifest: { ...f.manifest, installer: link } }), /regular file/);
  const tx = await f.prepare(), journal = readJSON(tx.journalPath);
  assert.equal(journal.sourceDir, f.source); assert.equal(journal.manifest.sha256, hash(f.installer));
  assert.equal(journal.status, "prepared"); assert.equal(journal.transactionId, tx.transactionId);
  assert.equal(Buffer.from(journal.launcherState.data, "base64").toString(), f.priorState);
  assert.equal(statSync(tx.nodePath).mode & 0o777, 0o700);
  assert.equal(pendingDesktopUpdate(f.target).journalPath, tx.journalPath);
  await assert.rejects(f.prepare(), /Another desktop update is active/);
});

linuxTest("actual detached helper waits for old process exit, atomically activates and accepts bound readiness", { timeout: 20_000 }, async (t) => {
  const f = fixture(t), old = processChild(process.execPath, ["-e", "setInterval(()=>{},1000)"]);
  t.after(async () => { old.child.kill("SIGKILL"); await old.exited; });
  const tx = await f.prepare({ pid: old.child.pid }), helper = f.start(tx);
  await until(() => readJSON(tx.journalPath).status === "waiting-for-exit", "helper did not wait for old process");
  assert.deepEqual(readFileSync(f.target), f.original);
  old.child.kill("SIGTERM"); await old.exited;
  const result = await helper.exited; assert.equal(result.code, 0, result.output);
  const journal = readJSON(tx.journalPath); assert.equal(journal.status, "completed", JSON.stringify(journal));
  assert.equal(hash(f.target), f.manifest.sha256); assert.equal(pendingDesktopUpdate(f.target), null);
  assert.equal(readFileSync(f.inner, "utf8"), "inner binary remains untouched");
  assert.equal(readFileSync(f.state, "utf8"), "candidate modified launcher state");
  assert.equal(f.launches().length, 1); const launched = f.launches()[0];
  assert.equal(launched.extract, "1"); assert.equal(launched.context.revision, REVISION); assert.equal(launched.job, "42");
  assert.equal(launched.context.sourceDir, f.source); assert.equal(launched.state, f.priorState);
  const env = candidateEnv(tx, f);
  assert.throws(() => acknowledgeDesktopUpdate({ env, revision: PREVIOUS }), /backend revision/);
  assert.throws(() => acknowledgeDesktopUpdate({ env: { ...env, MAGI_DESKTOP_UPDATE_ID: "wrong" }, revision: REVISION }), /identity mismatch/);
  assert.throws(() => acknowledgeDesktopUpdate({ env: { ...env, MAGI_DESKTOP_UPDATE_SHA256: "0".repeat(64) }, revision: REVISION }), /identity mismatch/);
  assert.throws(() => acknowledgeDesktopUpdate({ env, revision: REVISION, artifact: f.inner }), /artifact does not match/);
  assert.equal(acknowledgeDesktopUpdate({ env: {}, revision: REVISION }), false);
  assert.equal(desktopUpdateLaunchContext({ env: {} }), null);
  assert.equal((await runDesktopUpdate(tx.journalPath)).status, "completed", "completed recovery is idempotent");
});

for (const behavior of ["no-ack", "wrong-ack", "exit", "spawn-error"]) {
  linuxTest(`actual ${behavior} failure restores original AppImage and byte-identical launcher state before old relaunch`, { timeout: 20_000 }, async (t) => {
    const f = fixture(t, { behavior, timeoutMs: 300 });
    const tx = await f.prepare(), helper = f.start(tx), result = await helper.exited;
    assert.equal(result.code, 0, result.output);
    const journal = readJSON(tx.journalPath); assert.equal(journal.status, "rolled-back", JSON.stringify(journal));
    assert.deepEqual(readFileSync(f.target), f.original); assert.equal(readFileSync(f.state, "utf8"), f.priorState);
    assert.equal(statSync(f.state).mode & 0o777, 0o640); assert.equal(pendingDesktopUpdate(f.target), null);
    await until(() => f.launches().some((item) => item.mode === "rollback"), "previous image was not relaunched");
    const rollback = f.launches().find((item) => item.mode === "rollback");
    assert.equal(rollback.revision, undefined); assert.equal(rollback.revisionDir, undefined);
    assert.equal(rollback.context.rollback, true); assert.equal(rollback.context.revision, undefined);
    assert.equal(rollback.context.sourceDir, undefined); assert.equal(rollback.context.job, 42);
    assert.equal(rollback.context.error, journal.error); assert.equal(rollback.state, f.priorState);
    assert.match(journal.error, /timed out|identity mismatch|exited|ENOENT/);
  });
}

linuxTest("rollback restores originally absent launcher state rather than leaving candidate state", { timeout: 20_000 }, async (t) => {
  const f = fixture(t, { behavior: "exit", stateExists: false }), tx = await f.prepare();
  const result = await f.start(tx).exited; assert.equal(result.code, 0, result.output);
  assert.equal(readJSON(tx.journalPath).status, "rolled-back"); assert.equal(existsSync(f.state), false);
});

linuxTest("old desktop exit timeout leaves its original artifact and does not launch another copy", { timeout: 20_000 }, async (t) => {
  const f = fixture(t), old = processChild(process.execPath, ["-e", "setInterval(()=>{},1000)"]);
  t.after(async () => { old.child.kill("SIGKILL"); await old.exited; });
  const tx = await f.prepare({ pid: old.child.pid, exitTimeoutMs: 120 });
  const result = await f.start(tx).exited; assert.equal(result.code, 0, result.output);
  assert.equal(readJSON(tx.journalPath).status, "rolled-back");
  assert.deepEqual(readFileSync(f.target), f.original); assert.deepEqual(f.launches(), []);
  assert.equal(old.child.exitCode, null); assert.equal(old.child.signalCode, null);
});

linuxTest("a duplicate actual helper cannot race the active transaction", { timeout: 20_000 }, async (t) => {
  const f = fixture(t), old = processChild(process.execPath, ["-e", "setInterval(()=>{},1000)"]);
  t.after(async () => { old.child.kill("SIGKILL"); await old.exited; });
  const tx = await f.prepare({ pid: old.child.pid }), first = f.start(tx);
  await until(() => readJSON(tx.journalPath).status === "waiting-for-exit", "helper did not start");
  const duplicate = await f.start(tx).exited; assert.equal(duplicate.code, 1); assert.match(duplicate.output, /already running/);
  assert.deepEqual(readFileSync(f.target), f.original);
  old.child.kill("SIGTERM"); await old.exited;
  assert.equal((await first.exited).code, 0); assert.equal(readJSON(tx.journalPath).status, "completed", JSON.stringify(readJSON(tx.journalPath)));
});

linuxTest("recovery finishes persisted swap intent with original target continuously present", { timeout: 20_000 }, async (t) => {
  const f = fixture(t), tx = await f.prepare(), journal = readJSON(tx.journalPath);
  journal.status = "swapping"; writeFileSync(tx.journalPath, JSON.stringify(journal));
  const backup = path.join(path.dirname(f.target), `.${path.basename(f.target)}.${tx.transactionId}.previous`);
  linkSync(f.target, backup);
  assert.deepEqual(readFileSync(f.target), f.original);
  const result = await f.start(tx).exited; assert.equal(result.code, 0, result.output);
  assert.equal(readJSON(tx.journalPath).status, "completed", JSON.stringify(readJSON(tx.journalPath))); assert.equal(hash(f.target), f.manifest.sha256);
  assert.equal(existsSync(backup), false);
});

linuxTest("recovery also handles an older journal interrupted with a missing outer target", { timeout: 20_000 }, async (t) => {
  const f = fixture(t), tx = await f.prepare(), journal = readJSON(tx.journalPath);
  journal.status = "swapping"; writeFileSync(tx.journalPath, JSON.stringify(journal));
  renameSync(f.target, path.join(path.dirname(f.target), `.${path.basename(f.target)}.${tx.transactionId}.previous`));
  const result = await f.start(tx).exited; assert.equal(result.code, 0, result.output);
  assert.equal(readJSON(tx.journalPath).status, "completed", JSON.stringify(readJSON(tx.journalPath)));
});

linuxTest("cold-start recovery repairs missing durable files and waits for its current desktop before rollback", { timeout: 20_000 }, async (t) => {
  const f = fixture(t), tx = await f.prepare(), journal = readJSON(tx.journalPath);
  journal.status = "preparing"; writeFileSync(tx.journalPath, JSON.stringify(journal));
  rmSync(tx.nodePath); rmSync(tx.helperPath);
  const recovery = await prepareDesktopUpdateRecovery({ target: f.target, nodePath: process.execPath });
  assert.equal(recovery.helperRunning, false); assert.equal(existsSync(recovery.nodePath), true); assert.equal(existsSync(recovery.helperPath), true);
  const current = processChild(process.execPath, ["-e", "setInterval(()=>{},1000)"]);
  const helper = processChild(recovery.nodePath, [recovery.helperPath, "--run", recovery.journalPath, String(current.child.pid)]);
  t.after(async () => { current.child.kill("SIGKILL"); helper.child.kill("SIGKILL"); await Promise.all([current.exited, helper.exited]); });
  await until(() => pendingDesktopUpdate(f.target).helperRunning, "recovery helper did not take ownership");
  await sleep(100); assert.deepEqual(f.launches(), []);
  assert.equal((await prepareDesktopUpdateRecovery({ target: f.target, nodePath: process.execPath })).helperRunning, true);
  current.child.kill("SIGTERM"); await current.exited;
  const result = await helper.exited; assert.equal(result.code, 0, result.output);
  assert.equal(readJSON(tx.journalPath).status, "rolled-back"); assert.deepEqual(readFileSync(f.target), f.original);
});

linuxTest("rollback waits for a detached backend that ignores SIGTERM before relaunching old desktop", { timeout: 20_000 }, async (t) => {
  const f = fixture(t, { behavior: "detached-descendant", timeoutMs: 200 }), tx = await f.prepare();
  const result = await f.start(tx).exited; assert.equal(result.code, 0, result.output);
  const pid = Number(readFileSync(path.join(f.root, "descendant.pid"), "utf8"));
  t.after(() => killGroup(pid));
  const raw = existsSync(`/proc/${pid}/stat`) ? readFileSync(`/proc/${pid}/stat`, "utf8") : null;
  assert.ok(raw === null || raw.slice(raw.lastIndexOf(")") + 2).startsWith("Z"), "detached candidate backend survived rollback");
  assert.equal(readJSON(tx.journalPath).status, "rolled-back");
});

for (const role of ["shell", "agent"]) {
  linuxTest(`actual candidate owner and Eva ${role} backend stay reclaimable after desktop exits without ACK`, { timeout: 20_000 }, async (t) => {
    const f = fixture(t, { behavior: `owned-eva-${role}`, timeoutMs: 5000 }), tx = await f.prepare();
    const result = await f.start(tx).exited; assert.equal(result.code, 0, result.output);
    const journal = readJSON(tx.journalPath);
    const launcher = readJSON(path.join(f.root, "owned-launcher.json")), backend = readJSON(path.join(f.root, "owned-backend.json"));
    t.after(() => { for (const process of [launcher, backend]) { try { globalThis.process.kill(process.pid, "SIGKILL"); } catch (error) { if (error.code !== "ESRCH") throw error; } } });
    assert.equal(launcher.group, journal.candidateProcess.pid); assert.equal(backend.group, journal.candidateProcess.pid);
    assert.equal(backend.marker, String(journal.candidateProcess.pid)); assert.equal(backend.journal, undefined); assert.equal(backend.role, role);
    for (const { pid } of [launcher, backend]) {
      const raw = existsSync(`/proc/${pid}/stat`) ? readFileSync(`/proc/${pid}/stat`, "utf8") : null;
      assert.ok(raw === null || raw.slice(raw.lastIndexOf(")") + 2).startsWith("Z"), `${role} PID ${pid} survived rollback`);
    }
    assert.equal(journal.status, "rolled-back"); assert.deepEqual(readFileSync(f.target), f.original);
  });
}

linuxTest("crashed readiness watcher resumes the same candidate process and accepts its later ACK", { timeout: 20_000 }, async (t) => {
  const f = fixture(t, { behavior: "delayed-ack", timeoutMs: 5000 }), tx = await f.prepare(), first = f.start(tx);
  await until(() => readJSON(tx.journalPath).status === "awaiting-ready" && f.launches().length === 1, "candidate not running");
  const originalCandidate = readJSON(tx.journalPath).candidateProcess;
  first.child.kill("SIGKILL"); await first.exited;
  const result = await f.start(tx).exited; assert.equal(result.code, 0, result.output);
  const journal = readJSON(tx.journalPath); assert.equal(journal.status, "completed", JSON.stringify(journal));
  assert.deepEqual(journal.candidateProcess, originalCandidate); assert.equal(f.launches().length, 1);
});

linuxTest("concurrent recovery helpers cannot both replace a dead runner owner", { timeout: 20_000 }, async (t) => {
  const f = fixture(t, { behavior: "delayed-ack", timeoutMs: 5000 }), tx = await f.prepare();
  writeFileSync(path.join(path.dirname(tx.journalPath), "runner.json"), JSON.stringify({ pid: 2147483647, start: "0", token: "dead" }));
  const results = await Promise.all(Array.from({ length: 6 }, () => f.start(tx).exited));
  assert.equal(results.filter((item) => item.code === 0).length, 1, JSON.stringify(results));
  assert.ok(results.filter((item) => item.code !== 0).every((item) => /already running/.test(item.output)), JSON.stringify(results));
  assert.equal(readJSON(tx.journalPath).status, "completed", JSON.stringify(readJSON(tx.journalPath))); assert.equal(f.launches().length, 1);
});

linuxTest("a saved readiness ACK cannot commit after its candidate process has died", { timeout: 20_000 }, async (t) => {
  const f = fixture(t, { behavior: "ack-exit", timeoutMs: 5000 }), tx = await f.prepare(), helper = f.start(tx);
  await until(() => readJSON(tx.journalPath).status === "awaiting-ready" && f.launches().length === 1, "candidate did not reach readiness wait");
  helper.child.kill("SIGSTOP");
  writeFileSync(path.join(f.root, "ack-now"), "ready then exit");
  const pid = f.launches()[0].pid;
  await until(() => {
    try { const raw = readFileSync(`/proc/${pid}/stat`, "utf8"); return raw.slice(raw.lastIndexOf(")") + 2).startsWith("Z"); }
    catch (error) { if (error.code === "ENOENT") return true; throw error; }
  }, "candidate did not exit after its ACK");
  helper.child.kill("SIGCONT");
  const result = await helper.exited; assert.equal(result.code, 0, result.output);
  const journal = readJSON(tx.journalPath);
  assert.equal(journal.status, "rolled-back"); assert.match(journal.error, /exited before activation/);
  assert.deepEqual(readFileSync(f.target), f.original);
});

linuxTest("runtime, installer and old extraction can disappear after preparation without breaking detached activation", { timeout: 20_000 }, async (t) => {
  const f = fixture(t), extraction = path.join(f.root, "old-extraction"); mkdirSync(extraction);
  const oldNode = path.join(extraction, "node"); copyFileSync(process.execPath, oldNode); chmodSync(oldNode, 0o700);
  const tx = await f.prepare({ nodePath: oldNode });
  rmSync(extraction, { recursive: true }); rmSync(f.installer);
  const result = await f.start(tx).exited; assert.equal(result.code, 0, result.output);
  assert.equal(readJSON(tx.journalPath).status, "completed", JSON.stringify(readJSON(tx.journalPath)));
});

linuxTest("staged artifact corruption fails closed and restores the original without launching candidate", { timeout: 20_000 }, async (t) => {
  const f = fixture(t), tx = await f.prepare();
  const staged = path.join(path.dirname(f.target), `.${path.basename(f.target)}.${tx.transactionId}.next`);
  writeFileSync(staged, "corrupted staged artifact");
  const result = await f.start(tx).exited; assert.equal(result.code, 0, result.output);
  assert.equal(readJSON(tx.journalPath).status, "rolled-back"); assert.deepEqual(readFileSync(f.target), f.original);
  assert.equal(f.launches().filter((item) => item.mode === "candidate").length, 0);
});

linuxTest("malformed journals and caller-selected state paths cannot redirect filesystem changes", async (t) => {
  const f = fixture(t);
  await assert.rejects(f.prepare({ launcherStatePath: path.join(f.root, "other.json") }), /Shell profile/);
  const tx = await f.prepare(), journal = readJSON(tx.journalPath);
  journal.launcherState.path = path.join(f.root, "other.json"); writeFileSync(tx.journalPath, JSON.stringify(journal));
  await assert.rejects(runDesktopUpdate(tx.journalPath), /snapshot path/);
  assert.deepEqual(readFileSync(f.target), f.original); assert.equal(existsSync(journal.launcherState.path), false);
});

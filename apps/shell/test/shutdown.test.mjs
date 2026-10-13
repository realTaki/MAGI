import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, mkdir, readFile, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { createProcessOwner } from "../process-owner.mjs";
import { createSupervisor, prepareAndStartUser } from "../process.mjs";
import { pathToFileURL } from "node:url";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(check, message, timeout = 20_000) {
  const deadline = Date.now() + timeout;
  while (!await check()) { if (Date.now() > deadline) throw new Error(message); await sleep(20); }
}
async function live(pid) {
  try {
    process.kill(pid, 0);
    if (process.platform === "linux") {
      const stat = await readFile(`/proc/${pid}/stat`, "utf8");
      if (stat.slice(stat.lastIndexOf(")") + 2).startsWith("Z")) return false;
    }
    return true;
  } catch { return false; }
}
async function markers(file) { try { return (await readFile(file, "utf8")).trim().split("\n").map(JSON.parse); } catch { return []; } }
async function directory(t) {
  const root = await mkdtemp(path.join(tmpdir(), "magi-shutdown-"));
  t.after(() => rm(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }));
  return root;
}
async function treeFixture(root) {
  const script = path.join(root, "tree.mjs");
  await writeFile(script, `import {spawn} from 'node:child_process';import{appendFileSync,readFileSync}from'node:fs';
const [role,file]=process.argv.slice(2);appendFileSync(file,JSON.stringify({role,pid:process.pid})+'\\n');
if(role==='root')process.send?.({type:'ready'});
if(role==='root'||role==='exit-root')spawn(process.execPath,[import.meta.filename,'child',file],{stdio:'ignore'});
if(role==='child')spawn(process.execPath,[import.meta.filename,'leaf',file],{stdio:'ignore'});
if(role==='exit-root'){const timer=setInterval(()=>{if(readFileSync(file,'utf8').trim().split('\\n').length===3){clearInterval(timer);process.exit(0)}},10)}else{process.on('SIGTERM',()=>{});setInterval(()=>{},1000)}
`);
  return script;
}
async function outside(t, script, file) {
  const child = spawn(process.execPath, [script, "leaf", file], { detached: true, stdio: "ignore" });
  const exited = new Promise((resolve) => child.once("exit", resolve));
  t.after(async () => { if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL"); await exited; });
  await until(async () => (await markers(file)).length === 1, "outside fixture did not start");
  return child;
}

test("closing owned commands kills real children and grandchildren while unrelated and swap helpers survive", { timeout: 40_000 }, async (t) => {
  const root = await directory(t), script = await treeFixture(root), file = path.join(root, "owned.jsonl");
  const unrelated = await outside(t, script, path.join(root, "unrelated.jsonl"));
  const swap = await outside(t, script, path.join(root, "swap.jsonl"));
  const owner = createProcessOwner({ graceMs: 100, forceMs: 5000 }); t.after(() => owner.close());
  const command = owner.command(process.execPath, [script, "root", file], { cwd: root, env: process.env });
  void command.catch(() => {});
  await until(async () => (await markers(file)).length === 3, "owned process tree did not start");
  const pids = (await markers(file)).map((item) => item.pid);
  const first = owner.close(), second = owner.close(); assert.equal(first, second, "close is idempotent");
  await first; await assert.rejects(command, /Shell is closing/);
  await until(async () => (await Promise.all(pids.map(live))).every((value) => !value), "owned descendant remained live");
  assert.equal(await live(unrelated.pid), true); assert.equal(await live(swap.pid), true);
  assert.throws(() => owner.spawn(process.execPath, [script, "leaf", file]), /closing/);
  t.diagnostic(JSON.stringify({ owned: pids, unrelated: unrelated.pid, swap: swap.pid }));
});

test("an exiting command root cannot leave real orphan descendants behind", { timeout: 40_000 }, async (t) => {
  const root = await directory(t), script = await treeFixture(root), file = path.join(root, "orphan.jsonl");
  const owner = createProcessOwner({ graceMs: 100 }); t.after(() => owner.close());
  await owner.command(process.execPath, [script, "exit-root", file], { cwd: root, env: process.env });
  const pids = (await markers(file)).map((item) => item.pid); assert.equal(pids.length, 3);
  await until(async () => (await Promise.all(pids.map(live))).every((value) => !value), "orphan descendant remained live");
});

test("supervisor close waits for a real application tree and refuses later starts and restarts", { timeout: 40_000 }, async (t) => {
  const root = await directory(t), source = await treeFixture(root), cwd = path.join(root, "apps/asp"), entry = path.join(cwd, "dist/asp.js"), file = path.join(root, "app.jsonl");
  await mkdir(path.dirname(entry), { recursive: true }); await writeFile(entry, await readFile(source));
  const owner = createProcessOwner({ graceMs: 100, forceMs: 5000 });
  const supervisor = createSupervisor({ node: process.execPath, env: process.env, owner }); t.after(() => supervisor.close());
  const spec = { cwd, entry, args: ["root", file] };
  const app = await supervisor.start(spec); await app.whenReady();
  await until(async () => (await markers(file)).length === 3, "app descendants missing");
  const pids = (await markers(file)).map((item) => item.pid);
  const first = supervisor.close(); assert.equal(first, supervisor.close()); await first;
  assert.equal(supervisor.snapshots().length, 0);
  await until(async () => (await Promise.all(pids.map(live))).every((value) => !value), "application descendant survived");
  await assert.rejects(supervisor.start(spec), /closing/); await assert.rejects(supervisor.restart(app.id), /closing/);
});

test("User command requests execute under the same Shell owner and are cancelled with it", { timeout: 40_000 }, async (t) => {
  const root = await directory(t), tree = await treeFixture(root), cwd = path.join(root, "apps/user"), entry = path.join(cwd, "dist/user.js"), file = path.join(root, "host-command.jsonl");
  await mkdir(path.dirname(entry), { recursive: true });
  const module = pathToFileURL(path.resolve(import.meta.dirname, "../../user/dist/host.js")).href;
  await writeFile(entry, `import{connectHost}from ${JSON.stringify(module)};const host=connectHost(process.stdin,process.stdout);host.serve({run:()=>host.command({file:process.execPath,args:[${JSON.stringify(tree)},'root',${JSON.stringify(file)}],options:{cwd:${JSON.stringify(root)}}})});process.send({type:'ready'});setInterval(()=>{},1000);`);
  const owner = createProcessOwner({ graceMs: 100, forceMs: 5000 });
  const supervisor = createSupervisor({ node: process.execPath, env: process.env, owner }); t.after(() => supervisor.close());
  const user = await supervisor.start({ cwd, entry }, { host: { command: ({ file, args, options }) => owner.command(file, args, options) } });
  await user.whenReady(); const pending = user.invoke("run"); void pending.catch(() => {});
  await until(async () => (await markers(file)).length === 3, "User command descendants missing");
  const pids = (await markers(file)).map((item) => item.pid);
  await supervisor.close(); await assert.rejects(pending, /exited|closing/);
  await until(async () => (await Promise.all(pids.map(live))).every((value) => !value), "delegated command orphan survived");
});

test("interrupted cold installation is repaired on restart without starting User after close or deleting data", { timeout: 50_000 }, async (t) => {
  const root = await directory(t), checkout = path.join(root, "MAGI"), cwd = path.join(checkout, "apps/user"), entry = path.join(cwd, "dist/user.js");
  await mkdir(cwd, { recursive: true });
  const lock = { lockfileVersion: 3, packages: { "": {}, "node_modules/example": { version: "1.0.0" } } };
  await writeFile(path.join(checkout, "package-lock.json"), JSON.stringify(lock));
  await writeFile(path.join(checkout, "package.json"), JSON.stringify({ dependencies: { example: "1.0.0" } }));
  const data = path.join(root, "operator-data.db"); await writeFile(data, "persistent operator data");
  const script = await treeFixture(root), file = path.join(root, "install.jsonl"), npm = path.join(root, "npm-fixture.mjs");
  await writeFile(npm, `import {spawn}from'node:child_process';import{mkdirSync,writeFileSync}from'node:fs';import path from'node:path';
if(process.argv[2]==='ci'){mkdirSync('node_modules',{recursive:true});if(process.env.HANG_INSTALL==='1'){const child=spawn(process.execPath,[process.env.TREE_SCRIPT,'root',process.env.TREE_FILE],{stdio:'inherit'});setInterval(()=>{},1000)}else{mkdirSync('node_modules/example',{recursive:true});writeFileSync('node_modules/example/package.json',JSON.stringify({name:'example',version:'1.0.0'}));writeFileSync('node_modules/.package-lock.json',JSON.stringify({packages:{'node_modules/example':{version:'1.0.0'}}}));}}else{mkdirSync('dist',{recursive:true});writeFileSync('dist/user.js',"process.send({type:'ready'});process.on('SIGTERM',()=>process.exit());setInterval(()=>{},1000);");}
`);
  let userStarts = 0;
  const owner = createProcessOwner({ graceMs: 100, spawnProcess: (node, args, options) => { if (args[0] === entry) userStarts++; return spawn(node, args, options); } });
  const supervisor = createSupervisor({ node: process.execPath, env: process.env, owner });
  t.after(() => supervisor.close());
  const pending = prepareAndStartUser({ cwd, entry }, { node: process.execPath, npm, env: { ...process.env, HANG_INSTALL: "1", TREE_SCRIPT: script, TREE_FILE: file }, managed: true, command: owner.command }, supervisor);
  void pending.catch(() => {});
  await until(async () => (await markers(file)).length === 3, "cold-install descendant tree missing");
  const pids = (await markers(file)).map((item) => item.pid);
  await supervisor.close(); await assert.rejects(pending, /closing/);
  assert.equal(userStarts, 0); assert.equal(supervisor.snapshots().length, 0);
  await until(async () => (await Promise.all(pids.map(live))).every((value) => !value), "installation orphan survived close");
  assert.equal(JSON.parse(await readFile(path.join(checkout, ".magi-install-state.json"), "utf8")).phase, "installing");
  const nextOwner = createProcessOwner({ graceMs: 100 });
  const next = createSupervisor({ node: process.execPath, env: process.env, owner: nextOwner }); t.after(() => next.close());
  const user = await prepareAndStartUser({ cwd, entry }, { node: process.execPath, npm, env: process.env, managed: true, command: nextOwner.command }, next);
  await user.whenReady();
  assert.equal(JSON.parse(await readFile(path.join(checkout, ".magi-install-state.json"), "utf8")).phase, "complete");
  assert.equal(await readFile(data, "utf8"), "persistent operator data");
  await next.close();
});

test("prepare finishing after shutdown cannot spawn User", { timeout: 10_000 }, async (t) => {
  const root = await directory(t), cwd = path.join(root, "apps/user"), entry = path.join(cwd, "dist/user.js");
  await mkdir(path.dirname(entry), { recursive: true });
  let release, entered = false, spawns = 0;
  const owner = createProcessOwner({ spawnProcess: (...args) => { spawns++; return spawn(...args); } });
  const supervisor = createSupervisor({ node: process.execPath, env: process.env, owner }); t.after(() => supervisor.close());
  const pending = prepareAndStartUser({ cwd, entry }, { managed: false, command: async () => { entered = true; await new Promise((resolve) => { release = resolve; }); await writeFile(entry, "process.send({type:'ready'});"); } }, supervisor);
  void pending.catch(() => {});
  await until(() => entered, "prepare did not enter"); await supervisor.close(); release();
  await assert.rejects(pending, /closing/); assert.equal(spawns, 0);
});

import test from "node:test";
import { spawn, execFileSync } from "node:child_process";
import { Bus } from "@magi/bus";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, readFile, rm, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { describeApp } from "../app-spec.mjs";
import { createSupervisor } from "../process.mjs";
import { createAppRebuilder, parseRebuildLink, rebuildRequestKey } from "../rebuild.mjs";

function program(version, ready = true) {
  return `import { writeFileSync } from 'node:fs';
writeFileSync(process.env.STATE, JSON.stringify({version:${JSON.stringify(version)},pid:process.pid,cwd:process.cwd(),args:process.argv.slice(2),identity:process.env.IDENTITY}));
${ready ? 'process.send?.({type:"ready"});' : ''}
process.on('SIGTERM', () => process.exit());
setInterval(() => {},1000);`;
}

async function cleanup(supervisor, directory) {
  supervisor.stopAll();
  const deadline = Date.now() + 10_000;
  while (supervisor.snapshots().length > 0) {
    if (Date.now() > deadline) throw new Error("Test apps did not exit before cleanup");
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  await rm(directory, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}

async function fixture(t, { spawnProcess } = {}) {
  const directory = await mkdtemp(path.join(tmpdir(), "magi-shell-"));
  const supervisor = createSupervisor({ node: process.execPath, env: process.env, spawnProcess, readyTimeoutMs: 3000, stopTimeoutMs: 3000 });
  t.after(() => cleanup(supervisor, directory));
  const specs = {};
  const handles = {};
  for (const name of ["user", "asp", "eva", "other"]) {
    const kind = name === "other" ? "eva" : name;
    const cwd = path.join(directory, name, "MAGI", "apps", kind);
    await mkdir(path.join(cwd, "dist"), { recursive: true });
    const entry = path.join(cwd, "dist", `${kind}.js`);
    await writeFile(entry, program("old"));
    const spec = { entry, cwd, args: kind === "eva" ? [`@${name}.magi`, "base", "secret", "--workspace", path.join(directory, name)] : [], env: { STATE: path.join(directory, `${name}.json`), IDENTITY: name } };
    specs[name] = spec;
    handles[name] = await supervisor.start(spec);
    await handles[name].whenReady();
  }
  const states = async () => Object.fromEntries(await Promise.all(Object.entries(specs).map(async ([name, spec]) => [name, JSON.parse(await readFile(spec.env.STATE, "utf8"))])));
  return { directory, supervisor, specs, handles, states };
}

for (const [target, name] of [["self", "eva"], ["app", "user"], ["asp", "asp"]]) {
  test(`${target}: compile own tree, replace only target, preserve identity and data paths`, async (t) => {
    const f = await fixture(t);
    const before = await f.states();
    const calls = [], events = [];
    let adopted = false;
    const rebuild = createAppRebuilder({ supervisor: f.supervisor, tools: { node: process.execPath, npm: "npm-cli", git: "git", env: {} }, managed: () => true, pause: async () => {}, emit: (e) => events.push(e), onUser: async (handle) => { adopted = handle.id === f.handles.user.id; }, command: async (file, args, options) => {
      calls.push({ file, args, cwd: options.cwd });
      if (args.includes("rev-parse")) return options.cwd === path.resolve(f.specs.eva.cwd, "..", "..") ? "magi/eva" : `magi/${name}`;
      if (args.includes("status")) return "";
      if (args.includes("build")) await writeFile(f.specs[name].entry, program("new"));
      return "";
    } });
    const result = await rebuild(`magi://runtime/rebuild?target=${target}&handle=%40eva.magi`);
    assert.equal(result.phase, "ready");
    const after = await f.states();
    for (const key of Object.keys(before)) {
      if (key !== name) assert.deepEqual(after[key], before[key]);
      else {
        assert.equal(after[key].version, "new");
        assert.notEqual(after[key].pid, before[key].pid);
        assert.equal(after[key].cwd, before[key].cwd);
        assert.deepEqual(after[key].args, before[key].args);
        assert.equal(after[key].identity, before[key].identity);
      }
    }
    assert.equal(adopted, name === "user");
    assert.ok(calls.filter((c) => c.file === process.execPath).every((c) => c.cwd === path.resolve(f.specs[name].cwd, "..", "..") || (name === "user" && c.cwd === f.specs.user.cwd)));
    assert.deepEqual(events.map((e) => e.phase), target === "self" ? ["requested", "building", "testing", "building", "restarting", "ready"] : ["requested", "merging", "building", "testing", "building", "restarting", "ready"]);
    assert.equal((await f.supervisor.start(f.specs.other)).id, f.handles.other.id, "restored User adopts existing children");
  });
}

test("build failure preserves running apps and reports failed building", async (t) => {
  const f = await fixture(t), before = await f.states(), events = [];
  const rebuild = createAppRebuilder({ supervisor: f.supervisor, tools: {}, managed: () => true, pause: async () => {}, emit: (e) => events.push(e), onUser: async () => {}, command: async () => { throw new Error("compiler failed"); } });
  await assert.rejects(rebuild("magi://runtime/rebuild?target=self&handle=%40eva.magi"), /compiler failed/);
  assert.deepEqual(await f.states(), before);
  assert.equal(events.at(-1).failedPhase, "building");
  assert.equal(events.at(-1).phase, "failed");
  assert.equal(rebuild.status()[0].message, "compiler failed");
});

test("restart failure does not report ready or restart siblings", async (t) => {
  const f = await fixture(t), before = await f.states(), events = [];
  const rebuild = createAppRebuilder({ supervisor: f.supervisor, tools: {}, managed: () => true, pause: async () => {}, emit: (e) => events.push(e), onUser: async () => {}, command: async () => { await writeFile(f.specs.eva.entry, 'process.exit(7)'); return ""; } });
  await assert.rejects(rebuild("magi://runtime/rebuild?target=self&handle=%40eva.magi"), /exited before ready.*7/);
  for (const name of ["user", "asp", "other"]) assert.deepEqual((await f.states())[name], before[name]);
  assert.equal(events.at(-1).failedPhase, "restarting");
  assert.ok(!events.some((e) => e.phase === "ready"));
});

test("alive without ready times out", async (t) => {
  const f = await fixture(t);
  await writeFile(f.specs.eva.entry, program("unready", false));
  await assert.rejects(f.supervisor.restart(f.handles.eva.id), /did not become ready/);
});

test("strict link validation", () => {
  assert.equal(parseRebuildLink("magi://runtime/rebuild?target=self&handle=@eva.magi&target=asp"), null);
  assert.equal(parseRebuildLink("magi://runtime/rebuild?target=self&handle=../eva"), null);
  assert.equal(parseRebuildLink("https://runtime/rebuild?target=self&handle=@eva.magi"), null);
});

test("real User retires without stopping EVA and restores supervisor handles", async (t) => {
  const f = await fixture(t);
  f.supervisor.stop(f.handles.user.id);
  // A real User is a host, unlike the simple app fixtures above.
  const repository = path.resolve(import.meta.dirname, "..", "..", "..");
  const home = path.join(f.directory, "home");
  const persisted = new Bus("@user.magi", path.join(home, ".magi", "user"));
  persisted.settings.set("rebuild-test", "preserved");
  persisted.close();
  const spec = {
    entry: path.join(repository, "apps", "user", "dist", "user.js"),
    cwd: path.join(repository, "apps", "user"),
    env: {
      MAGI_CHECKOUT: repository, MAGI_APP_CHECKOUT: repository,
      MAGI_HOME: home, MAGI_USER_DATA: path.join(home, ".magi", "user", "electron"),
      MAGI_REPOSITORY: "https://github.com/realTaki/MAGI.git",
      MAGI_GIT: "git", MAGI_NODE: process.execPath,
      MAGI_NPM: path.join(repository, "apps", "shell", "runtime", "npm", "node_modules", "npm", "bin", "npm-cli.js"),
    },
  };
  // Omit the ASP fixture: its fake process has no health endpoint.
  f.supervisor.stop(f.handles.asp.id);
  while (f.supervisor.snapshots().some((p) => [f.handles.asp.id, f.handles.user.id].includes(p.id))) await new Promise((r) => setTimeout(r, 10));
  const before = await f.states();
  const user = await f.supervisor.start(spec, { host: { emit: () => {}, progress: () => {} } });
  await user.whenReady();
  await user.invoke("ready");
  const data = path.join(home, ".magi", "user", "memories", "books.db");
  assert.ok((await readFile(data)).length > 0);
  const restored = await f.supervisor.restart(user.id);
  await restored.invoke("ready");
  assert.equal(restored.id, user.id);
  for (const name of ["eva", "other"]) assert.deepEqual((await f.states())[name], before[name]);
  assert.ok((await readFile(data)).length > 0);
  await restored.invoke("shutdown");
  while (f.supervisor.snapshots().some((p) => [f.handles.eva.id, f.handles.other.id].includes(p.id))) await new Promise((r) => setTimeout(r, 10));
  f.supervisor.stop(restored.id);
  while (f.supervisor.snapshots().some((p) => p.id === restored.id)) await new Promise((r) => setTimeout(r, 10));
  await assert.rejects(restored.invoke("ready"), /exited/);
  const reopened = new Bus("@user.magi", path.join(home, ".magi", "user"));
  assert.equal(reopened.settings.get("rebuild-test"), "preserved");
  reopened.close();
});

for (const name of ["user", "asp"]) {
  test(`${name} self rebuild uses its registered worktree without a merge`, async (t) => {
    const f = await fixture(t), calls = [];
    const rebuild = createAppRebuilder({ supervisor: f.supervisor, tools: {}, managed: () => true, pause: async () => {}, emit: () => {}, onUser: async () => {}, command: async (_file, args, options) => {
      calls.push({ args, cwd: options.cwd });
      await writeFile(f.specs[name].entry, program("new")); return "";
    } });
    await rebuild(`magi://runtime/rebuild?target=self&handle=@${name}.magi`);
    assert.ok(!calls.some(({ args }) => args.includes("merge")));
    assert.equal((await f.states())[name].version, "new");
  });
}

test("unowned target and unmanaged checkout are rejected before commands", async (t) => {
  const f = await fixture(t);
  let commands = 0;
  for (const managed of [true, false]) {
    const rebuild = createAppRebuilder({ supervisor: f.supervisor, tools: {}, managed: () => managed, pause: async () => {}, emit: () => {}, onUser: async () => {}, command: async () => { commands++; } });
    await assert.rejects(rebuild("magi://runtime/rebuild?target=self&handle=@unknown.magi"));
  }
  assert.equal(commands, 0);
});

test("failed merge aborts its transaction and leaves every app running", async (t) => {
  const f = await fixture(t), before = await f.states(), calls = [];
  const rebuild = createAppRebuilder({ supervisor: f.supervisor, tools: { git: "git" }, managed: () => true, pause: async () => {}, emit: () => {}, onUser: async () => {}, command: async (_file, args, options) => {
    calls.push(args);
    if (args.includes("rev-parse")) return options.cwd === path.resolve(f.specs.eva.cwd, "..", "..") ? "magi/eva" : "magi/asp";
    if (args.includes("status")) return "";
    if (args.includes("--no-edit")) throw new Error("merge conflict");
    return "";
  } });
  await assert.rejects(rebuild("magi://runtime/rebuild?target=asp&handle=@eva.magi"), /merge conflict/);
  assert.ok(calls.some((args) => args.includes("--abort")));
  assert.ok(!calls.some((args) => args.includes("build")));
  assert.deepEqual(await f.states(), before);
  assert.equal(rebuild.status()[0].failedPhase, "merging");
});

test("concurrent supervisor restarts share one replacement", async (t) => {
  const f = await fixture(t), before = await f.states();
  const first = f.supervisor.restart(f.handles.eva.id);
  const second = f.supervisor.restart(f.handles.eva.id);
  assert.equal(first, second);
  const [a, b, adopted] = await Promise.all([first, second, f.supervisor.start(f.specs.eva)]);
  assert.equal(a.id, b.id);
  assert.equal(a.id, adopted.id);
  assert.equal(f.supervisor.snapshots().filter((p) => p.spec.args?.[0] === "@eva.magi").length, 1);
  assert.notEqual((await f.states()).eva.pid, before.eva.pid);
});

test("non-exiting old instance prevents replacement", { skip: process.platform === "win32" ? "Windows SIGTERM forcibly terminates the fixture; ignored POSIX signals do not apply" : false }, async (t) => {
  const directory = await mkdtemp(path.join(tmpdir(), "magi-stop-test-"));
  const supervisor = createSupervisor({ node: process.execPath, env: process.env, stopTimeoutMs: 100, readyTimeoutMs: 1000 });
  const cwd = path.join(directory, "apps", "eva");
  await mkdir(path.join(cwd, "dist"), { recursive: true });
  const entry = path.join(cwd, "dist", "eva.js"), marker = path.join(directory, "started");
  await writeFile(entry, `import { appendFileSync } from 'node:fs'; appendFileSync(${JSON.stringify(marker)},process.pid+'\\n'); process.on('SIGTERM',()=>{}); process.send({type:'ready'}); setInterval(()=>{},1000);`);
  const handle = await supervisor.start({ entry, cwd, args: ["@slow.magi"] });
  await handle.whenReady();
  const old = await readFile(marker, "utf8");
  t.after(async () => { process.kill(Number(old.trim()), "SIGKILL"); await rm(directory, { recursive: true, force: true }); });
  await assert.rejects(supervisor.restart(handle.id), /did not stop/);
  assert.equal(await readFile(marker, "utf8"), old, "no replacement was spawned");
});

test("rebuild rejects overlapping transactions and conflicting identities", async (t) => {
  const f = await fixture(t);
  let release;
  const paused = new Promise((resolve) => { release = resolve; });
  const rebuild = createAppRebuilder({ supervisor: f.supervisor, tools: {}, managed: () => true, pause: () => paused, emit: () => {}, onUser: async () => {}, command: async () => "" });
  const first = rebuild("magi://runtime/rebuild?target=self&handle=@eva.magi");
  await assert.rejects(rebuild("magi://runtime/rebuild?target=self&handle=@eva.magi"), /still running/);
  await assert.rejects(f.supervisor.start({ ...f.specs.eva, args: ["@eva.magi", "different-token"] }), /Conflicting process/);
  release();
  await first;
});

test("same-name scripts cannot become lifecycle targets; wrong publishing branch is refused", async () => {
  const specs = [
    { id: "1", spec: { cwd: "/tmp/unrelated", entry: "eva.js", args: ["@eva.magi"] } },
    { id: "2", spec: { cwd: "/tmp/root/apps/asp", entry: "dist/asp.js" } },
  ];
  let commands = 0;
  const make = (processes, branch) => createAppRebuilder({ supervisor: { snapshots: () => processes }, tools: {}, managed: () => true, pause: async () => {}, emit: () => {}, onUser: async () => {}, command: async () => { commands++; return branch; } });
  await assert.rejects(make(specs)("magi://runtime/rebuild?target=self&handle=@eva.magi"), /not uniquely owned/);
  assert.equal(commands, 0);
  specs[0].spec = { cwd: "/tmp/root/apps/eva", entry: "dist/eva.js", args: ["@eva.magi"] };
  await assert.rejects(make(specs, "main")("magi://runtime/rebuild?target=asp&handle=@eva.magi"), /named branch/);
  assert.equal(commands, 1, "no merge or build after wrong branch");
});

test("fresh compiled EVA and ASP become ready with isolated persistent state", async (t) => {
  const directory = await mkdtemp(path.join(tmpdir(), "magi-real-apps-"));
  const repository = path.resolve(import.meta.dirname, "../../..");
  const supervisor = createSupervisor({ node: process.execPath, env: process.env, readyTimeoutMs: 5000 });
  t.after(() => cleanup(supervisor, directory));
  const aspCwd = path.join(directory, "apps", "asp");
  await mkdir(path.join(aspCwd, "dist"), { recursive: true });
  const databasePath = path.join(directory, "data", "asp.sqlite");
  const compiledAsp = pathToFileURL(path.join(repository, "apps", "asp", "dist", "asp.js")).href;
  await writeFile(path.join(aspCwd, "dist", "asp.js"), `import { Asp } from ${JSON.stringify(compiledAsp)}; const asp=new Asp({databasePath:${JSON.stringify(databasePath)},workspace:${JSON.stringify(path.join(directory, "asp-state"))},host:'127.0.0.1',port:0}); await asp.start(); process.send({type:'ready'}); process.on('SIGTERM',async()=>{await asp.stop();process.exit();});`);
  const asp = await supervisor.start({ cwd: aspCwd, entry: "dist/asp.js" });
  await asp.whenReady();
  const workspace = path.join(directory, "eva-state");
  const eva = await supervisor.start({ cwd: path.join(repository, "apps", "eva"), entry: "dist/eva.js", args: ["@isolated.magi", "http://127.0.0.1:9", "test-token", "--workspace", workspace] });
  await eva.whenReady();
  assert.ok((await readFile(databasePath)).length > 0);
  assert.ok((await readFile(path.join(workspace, "memories", "books.db"))).length > 0);
  await supervisor.restart(eva.id);
  await supervisor.restart(asp.id);
  assert.equal(supervisor.snapshots().length, 2);
});

test("Windows app paths retain independent worktrees and reject same-name scripts", () => {
  const cwd = String.raw`C:\Users\tester\.magi\eva-000\MAGI\apps\eva`;
  const app = describeApp({ cwd, entry: String.raw`dist\eva.js`, args: ["@eva-000.magi"] }, path.win32);
  assert.equal(app.root, String.raw`C:\Users\tester\.magi\eva-000\MAGI`);
  assert.equal(app.key, "eva:@eva-000.magi");
  assert.equal(describeApp({ cwd, entry: String.raw`C:\other\eva.js`, args: ["@eva-000.magi"] }, path.win32), null);
});

test("equivalent URLs have one duplicate request key", () => {
  assert.equal(rebuildRequestKey("magi://runtime/rebuild?handle=%40eva.magi&target=self"),
    rebuildRequestKey("magi://runtime/rebuild/?target=self&handle=@eva.magi"));
  assert.equal(parseRebuildLink("magi://runtime:123/rebuild?target=self&handle=@eva.magi"), null);
});

test("publishing refuses a target on another branch before merge", async () => {
  let calls = 0;
  const rebuild = createAppRebuilder({ supervisor: { snapshots: () => [
    { id: "eva", spec: { cwd: "/tmp/eva/MAGI/apps/eva", entry: "dist/eva.js", args: ["@eva.magi"] } },
    { id: "asp", spec: { cwd: "/tmp/asp/MAGI/apps/asp", entry: "dist/asp.js" } },
  ] }, tools: {}, managed: () => true, pause: async () => {}, emit: () => {}, onUser: async () => {}, command: async () => ++calls === 1 ? "magi/eva" : "main" });
  await assert.rejects(rebuild("magi://runtime/rebuild?target=asp&handle=@eva.magi"), /own managed branch/);
  assert.equal(calls, 2);
});

test("stop followed immediately by start waits for the old instance", async (t) => {
  const f = await fixture(t), before = await f.states();
  f.supervisor.stop(f.handles.eva.id);
  const replacement = await f.supervisor.start(f.specs.eva);
  await replacement.whenReady();
  assert.notEqual(replacement.id, f.handles.eva.id, "a stopping instance must not be adopted");
  assert.notEqual((await f.states()).eva.pid, before.eva.pid);
  assert.equal(f.supervisor.snapshots().filter((p) => p.spec.args?.[0] === "@eva.magi").length, 1);
});

test("a broken User host pipe cannot take down the Shell supervisor", { timeout: 10_000 }, async (t) => {
  let userChild;
  const f = await fixture(t, { spawnProcess: (node, args, options) => {
    const child = spawn(node, args, options);
    if (options.stdio[0] === "pipe") userChild = child;
    return child;
  } });
  f.supervisor.stop(f.handles.user.id);
  while (f.supervisor.snapshots().some((p) => p.id === f.handles.user.id)) await new Promise((r) => setTimeout(r, 10));
  const cwd = path.join(f.directory, "broken", "apps", "user");
  await mkdir(path.join(cwd, "dist"), { recursive: true });
  const entry = path.join(cwd, "dist", "user.js");
  await writeFile(entry, "process.send({type:'ready'}); setInterval(()=>{},1000);");
  const host = await f.supervisor.start({ cwd, entry }, { host: {} });
  await host.whenReady();
  userChild.stdin.destroy(); // Close the real host write pipe on every platform.
  await assert.rejects(host.invoke("ready"), /EPIPE|closed|exited|EOF|broken pipe|write/i);
  f.supervisor.stop(f.handles.eva.id);
  while (f.supervisor.snapshots().some((p) => p.id === f.handles.eva.id)) await new Promise((r) => setTimeout(r, 10));
  assert.ok(f.supervisor.snapshots().some((p) => p.id === f.handles.other.id), "other peers remain owned");
  host.stop();
});

test("compiled EVA starts through a directory alias instead of silently skipping main", async (t) => {
  const directory = await mkdtemp(path.join(tmpdir(), "magi-entry-alias-"));
  const repository = path.resolve(import.meta.dirname, "../../..");
  const alias = path.join(directory, "compiled");
  await symlink(path.join(repository, "apps", "eva", "dist"), alias, "junction");
  const supervisor = createSupervisor({ node: process.execPath, env: process.env, readyTimeoutMs: 5000 });
  t.after(() => cleanup(supervisor, directory));
  const state = path.join(directory, "state");
  const child = await supervisor.start({ cwd: path.join(repository, "apps", "eva"), entry: path.join(alias, "eva.js"), args: ["@alias.magi", "http://127.0.0.1:9", "test-token", "--workspace", state] });
  await child.whenReady();
  assert.ok((await readFile(path.join(state, "memories", "books.db"))).length > 0);
});

test("private rebuild IPC binds the caller identity and refuses spoofed or unowned entries", async (t) => {
  const directory = await mkdtemp(path.join(tmpdir(), "magi-lifecycle-ipc-"));
  const supervisor = createSupervisor({ node: process.execPath, env: process.env });
  t.after(() => cleanup(supervisor, directory));
  const received = [];
  supervisor.setRebuildHandler((request) => received.push(request));
  const cases = [
    { kind: "eva", name: "owned", request: { type: "runtime.rebuild", id: "owned", target: "self" }, accepted: true },
    { kind: "eva", name: "spoofed", request: { type: "runtime.rebuild", id: "spoofed", target: "self", handle: "@other.magi" } },
    { kind: "user", name: "wrong-role", request: { type: "runtime.rebuild", id: "wrong-role", target: "self" } },
    { kind: "unrelated", name: "same-name", request: { type: "runtime.rebuild", id: "same-name", target: "self" } },
  ];
  for (const item of cases) {
    const cwd = path.join(directory, item.name, "apps", item.kind), result = path.join(directory, `${item.name}.json`);
    await mkdir(path.join(cwd, "dist"), { recursive: true });
    const entry = path.join(cwd, "dist", item.kind === "unrelated" ? "eva.js" : `${item.kind}.js`);
    await writeFile(entry, `import {writeFileSync} from 'node:fs'; process.on('message',message=>{if(message.type==='runtime.rebuild.accepted')writeFileSync(${JSON.stringify(result)},JSON.stringify(message));}); process.send({type:'ready'}); process.send(${JSON.stringify(item.request)}); setInterval(()=>{},1000);`);
    const child = await supervisor.start({ cwd, entry, args: [`@${item.name}.magi`] }); await child.whenReady();
    let reply;
    for (let attempt = 0; attempt < 100 && !reply; attempt++) { try { reply = JSON.parse(await readFile(result, "utf8")); } catch { await new Promise((resolve) => setTimeout(resolve, 20)); } }
    assert.ok(reply, "IPC response arrived");
    assert.equal(Boolean(reply.error), !item.accepted);
  }
  assert.deepEqual(received, [{ target: "self", handle: "@owned.magi" }]);
});

test("failed tests and failed compilation retain the old instance", async (t) => {
  const f = await fixture(t), before = await f.states();
  for (const failing of ["test", "build"]) {
    const events = [];
    const rebuild = createAppRebuilder({ supervisor: f.supervisor, tools: { npm: "npm-cli" }, managed: () => true, pause: async () => {}, emit: (event) => events.push(event), onUser: async () => {}, command: async (_file, args) => {
      if ((failing === "test" && args[1] === "test") || (failing === "build" && args[2] === "build")) throw new Error(`rejected ${failing}`);
      return "";
    } });
    await assert.rejects(rebuild("magi://runtime/rebuild?target=self&handle=@eva.magi"), new RegExp(`rejected ${failing}`));
    assert.equal(events.at(-1).failedPhase, failing === "test" ? "testing" : "building");
    assert.deepEqual(await f.states(), before);
    assert.ok(!events.some((event) => event.phase === "restarting"));
  }
});

test("a supervised rebuild caller without IPC fails closed", () => {
  const repository = path.resolve(import.meta.dirname, "../../..");
  const module = pathToFileURL(path.join(repository, "packages/built-in-tools/dist/rebuild.js")).href;
  const program = `import {rebuildTools} from ${JSON.stringify(module)}; try {await rebuildTools('@closed.magi')[0].run({target:'self'});process.exitCode=1;}catch(error){console.log(error.message);}`;
  const output = execFileSync(process.execPath, ["--input-type=module", "-e", program], { env: { ...process.env, MAGI_SHELL_REBUILD: "ipc" }, encoding: "utf8" });
  assert.match(output, /Shell lifecycle IPC is disconnected/);
});

test("hand-run EVA exits its CLI without a service IPC listener", { timeout: 10_000 }, async (t) => {
  const directory = await mkdtemp(path.join(tmpdir(), "magi-cli-exit-"));
  const repository = path.resolve(import.meta.dirname, "../../..");
  const child = spawn(process.execPath, [path.join(repository, "apps/eva/dist/eva.js"), "@cli.magi", "--workspace", directory], { stdio: ["pipe", "pipe", "pipe"] });
  const exit = new Promise((resolve, reject) => { child.once("exit", (code) => resolve(code)); child.once("error", reject); });
  t.after(async () => { if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL"); await exit; await rm(directory, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); });
  let sent = false;
  child.stdout.on("data", (data) => { if (!sent && String(data).includes("> ")) { sent = true; child.stdin.write("/exit\n"); } });
  assert.equal(await exit, 0);
  assert.equal(sent, true);
});

test("EVA that stalls before ready can still be retired", { timeout: 15_000 }, async (t) => {
  const directory = await mkdtemp(path.join(tmpdir(), "magi-startup-stop-"));
  const repository = path.resolve(import.meta.dirname, "../../..");
  const cwd = path.join(directory, "apps/eva"), marker = path.join(directory, "starting");
  await mkdir(path.join(cwd, "dist"), { recursive: true });
  await symlink(path.join(repository, "node_modules"), path.join(directory, "node_modules"), "junction");
  const original = await readFile(path.join(repository, "apps/eva/dist/eva.js"), "utf8");
  const stalled = original.replace("await magi.start();", `realpathSync(${JSON.stringify(directory)}); (await import('node:fs')).writeFileSync(${JSON.stringify(marker)},'starting'); await new Promise(()=>{});`);
  assert.notEqual(stalled, original);
  const entry = path.join(cwd, "dist/eva.js"); await writeFile(entry, stalled);
  let child;
  const supervisor = createSupervisor({ node: process.execPath, env: process.env, readyTimeoutMs: 2000, spawnProcess: (...args) => { child = spawn(...args); return child; } });
  t.after(async () => { if (child?.exitCode === null && child.signalCode === null) child.kill("SIGKILL"); await cleanup(supervisor, directory); });
  const handle = await supervisor.start({ cwd, entry, args: ["@stalled.magi", "http://127.0.0.1:9", "local-only", "--workspace", path.join(directory, "state")] });
  for (let attempt = 0; attempt < 300; attempt++) { try { await readFile(marker); break; } catch { await new Promise((resolve) => setTimeout(resolve, 20)); } }
  assert.equal(await readFile(marker, "utf8"), "starting");
  await assert.rejects(handle.whenReady(), /did not become ready/);
  supervisor.stop(handle.id);
  for (let attempt = 0; attempt < 100 && supervisor.snapshots().length; attempt++) await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(supervisor.snapshots().length, 0, "a not-ready process must not swallow its stop signal");
});

test("source rebuild reuses live dependencies and rejects changed declarations", async (t) => {
  const directory = await mkdtemp(path.join(tmpdir(), "magi-dependency-gate-"));
  const root = path.join(directory, "MAGI"), cwd = path.join(root, "apps/eva"), state = path.join(directory, "state.json");
  await mkdir(path.join(cwd, "dist"), { recursive: true }); await mkdir(path.join(root, "node_modules"));
  await writeFile(path.join(root, "package-lock.json"), "{}"); await writeFile(path.join(root, "package.json"), JSON.stringify({ dependencies: { example: "1" } }));
  const entry = path.join(cwd, "dist/eva.js"); await writeFile(entry, program("old"));
  const supervisor = createSupervisor({ node: process.execPath, env: process.env }); t.after(() => cleanup(supervisor, directory));
  const handle = await supervisor.start({ cwd, entry, args: ["@dependencies.magi"], env: { STATE: state } }); await handle.whenReady();
  const calls = [];
  const rebuild = createAppRebuilder({ supervisor, tools: { npm: "npm-cli" }, managed: () => true, pause: async () => {}, emit: () => {}, onUser: async () => {}, command: async (_file, args) => { calls.push(args); if (args[2] === "build") await writeFile(entry, program("new")); return ""; } });
  await rebuild("magi://runtime/rebuild?target=self&handle=@dependencies.magi");
  assert.ok(!calls.some((args) => args.includes("ci")), "loaded native modules are never removed for source-only edits");
  const before = JSON.parse(await readFile(state, "utf8"));
  for (const file of ["package.json", "package-lock.json"]) {
    const saved = await readFile(path.join(root, file));
    await writeFile(path.join(root, file), file === "package.json" ? JSON.stringify({ dependencies: { example: "2" } }) : '{"changed":true}');
    const count = calls.length;
    await assert.rejects(rebuild("magi://runtime/rebuild?target=self&handle=@dependencies.magi"), /Dependencies changed/);
    assert.equal(calls.length, count); assert.deepEqual(JSON.parse(await readFile(state, "utf8")), before);
    await writeFile(path.join(root, file), saved);
  }
  assert.equal(supervisor.snapshots().length, 1);
});

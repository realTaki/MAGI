import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

import { EVA_MAGI_HANDLE, magiLocalName, SHELL_MAGI_HANDLE } from "@magi/bus";
import { agentBranch, agentSource, createMagiRuntime } from "@magi/webapp/magi-runtime.js";

const exec = promisify(execFile);
const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const agent = { handle: EVA_MAGI_HANDLE, token: "tok" };

// Node is supplied by the project test runner; npm execution is stubbed.
const testRuntime = { node: process.execPath, npm: fileURLToPath(import.meta.url) };

async function git(cwd, args) {
  return (await exec("git", args, { cwd })).stdout;
}

async function makeOrigin(root, autocrlf = false) {
  const origin = path.join(root, "origin.git");
  await git(root, ["init", "--quiet", "--bare", origin]);
  const seed = path.join(root, "seed");
  await git(root, ["clone", "--quiet", origin, seed]);
  await git(seed, ["config", "core.autocrlf", String(autocrlf)]);
  await git(seed, ["config", "user.email", "a@b"]);
  await git(seed, ["config", "user.name", "a"]);
  writeFileSync(path.join(seed, "README.md"), "one\n");
  await git(seed, ["add", "."]);
  await git(seed, ["commit", "--quiet", "-m", "one"]);
  await git(seed, ["branch", "-M", "main"]);
  await git(seed, ["push", "--quiet", "-u", "origin", "HEAD:main"]);
  await git(origin, ["symbolic-ref", "HEAD", "refs/heads/main"]);
  return { origin, seed };
}

/** Records managed Node/npm calls while letting Git use the real executable. */
function recorder() {
  const npm = [];
  const spawns = [];
  const run = async (binary, args, options) => {
    if (args[0] === testRuntime.npm) {
      npm.push({ args: args.slice(1), cwd: options.cwd });
      return "";
    }
    try {
      const result = await exec(binary, args, { cwd: options.cwd, env: { ...process.env, ...options.env } });
      return `${result.stdout}`;
    } catch (error) {
      throw new Error(`${options.description} (${error.code ?? "?"})`);
    }
  };
  const spawnProcess = (binary, args, options) => {
    const child = new EventEmitter();
    child.pid = 4242 + spawns.length;
    child.exitCode = null;
    child.signalCode = null;
    child.stderr = new EventEmitter();
    child.kill = () => {
      child.signalCode = "SIGTERM";
      return true;
    };
    spawns.push({ binary, args, options, child });
    return child;
  };
  return { run, spawnProcess, npm, spawns };
}

function runtimeWith(origin, home, recorded, log = () => {}) {
  return createMagiRuntime({
    checkout: origin,
    repository: origin,
    home,
    base: "http://127.0.0.1:42069",
    tools: { git: "git", env: process.env, ...testRuntime },
    run: recorded.run,
    spawnProcess: recorded.spawnProcess,
    managed: true,
    log,
  });
}

test("every MAGI gets its own branch checked out inside its workspace", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-source-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const { origin } = await makeOrigin(root);
  const home = path.join(root, "home");
  const recorded = recorder();
  const runtime = runtimeWith(origin, home, recorded);

  await runtime.ensureSource(agent.handle);
  await assert.rejects(runtime.start(agent), /eva --role agent/);

  const source = agentSource(home, agent.handle);
  assert.equal(source, path.join(home, ".magi", magiLocalName(agent.handle), "MAGI"));
  assert.equal(existsSync(path.join(source, ".git")), true, "the clone has its own .git directory");
  assert.equal((await git(source, ["rev-parse", "--abbrev-ref", "HEAD"])).trim(), agentBranch(agent.handle));
  assert.equal((await git(source, ["rev-parse", "--is-inside-work-tree"])).trim(), "true");
  assert.equal((await git(origin, ["branch", "--list", agentBranch(agent.handle)])).trim(), "");

  // Dependencies belong to that checkout, and the process runs from it.
  assert.deepEqual(recorded.npm, [
    // `--ignore-scripts` keeps a desktop install from reaching node-gyp.
    // The node builds itself; this checkout is not compiled here.
    { args: ["ci", "--ignore-scripts"], cwd: source },
  ]);
  assert.equal(recorded.spawns.length, 0);
  assert.deepEqual(runtime.running(), []);
});

test("in-process hosts are not spawned as eva.js children", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-host-skip-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const { origin } = await makeOrigin(root);
  const home = path.join(root, "home");
  const recorded = recorder();
  const runtime = runtimeWith(origin, home, recorded);

  const started = await runtime.startAll([
    { handle: SHELL_MAGI_HANDLE, token: "tok-0" },
    { handle: EVA_MAGI_HANDLE, token: "tok-2" },
  ]);
  assert.deepEqual(started.started, []);
  assert.equal(started.failed.length, 1);
  assert.equal(started.failed[0].handle, EVA_MAGI_HANDLE);
  assert.match(started.failed[0].detail, /eva --role agent/);
  assert.equal(recorded.spawns.length, 0);
  assert.deepEqual(runtime.running(), []);
  assert.deepEqual(await runtime.start({ handle: SHELL_MAGI_HANDLE, token: "tok-0" }), {
    handle: SHELL_MAGI_HANDLE,
    started: false,
  });
  assert.equal(runtime.stop(SHELL_MAGI_HANDLE), false);
  assert.equal(recorded.spawns.length, 0);
});

test("a checkout that cannot be cloned fails instead of sharing another tree", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-clone-fail-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const recorded = recorder();
  const runtime = createMagiRuntime({
    checkout: path.join(root, "checkout"),
    repository: path.join(root, "missing.git"),
    home: path.join(root, "home"),
    base: "http://127.0.0.1:42069",
    tools: { git: "git", env: process.env, ...testRuntime },
    run: recorded.run,
    spawnProcess: recorded.spawnProcess,
    managed: true,
    log: () => {},
  });

  await assert.rejects(runtime.ensureSource(agent.handle), /Could not clone/);
  assert.equal(recorded.spawns.length, 0);
});

const autocrlf = process.platform === "win32";
test(`merging advances only the requested MAGI source branch (autocrlf=${autocrlf})`, async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-merge-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const { origin, seed } = await makeOrigin(root, autocrlf);
  const recorded = recorder();
  const runtime = runtimeWith(origin, path.join(root, "home"), recorded);
  await runtime.ensureSource(agent.handle);
  const source = agentSource(path.join(root, "home"), agent.handle);
  if (autocrlf) await git(source, ["config", "core.autocrlf", "true"]);

  writeFileSync(path.join(seed, "README.md"), "two\n");
  await git(seed, ["add", "."]);
  await git(seed, ["commit", "--quiet", "-m", "two"]);
  await git(seed, ["push", "--quiet", "origin", "main"]);

  const merged = await runtime.merge(agent);
  assert.deepEqual(merged, { handle: agent.handle, merged: true, from: "origin/main" });
  assert.equal(readFileSync(path.join(source, "README.md"), "utf8").replace(/\r\n/g, "\n"), "two\n");
  assert.equal(await git(source, ["show", "HEAD:README.md"]), "two\n", "Git blob retains the exact committed content");
  if (autocrlf) assert.equal(readFileSync(path.join(source, "README.md"), "utf8"), "two\r\n", "fixture really exercises CRLF checkout");
  assert.equal(recorded.spawns.length, 0, "the shell does not restart another node");

  assert.deepEqual(await runtime.merge(agent), { handle: agent.handle, merged: false, from: "origin/main" });
  assert.equal(recorded.spawns.length, 0);
});

test(`a conflicting merge is aborted and reported (autocrlf=${autocrlf})`, async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-conflict-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const { origin, seed } = await makeOrigin(root, autocrlf);
  const recorded = recorder();
  const runtime = runtimeWith(origin, path.join(root, "home"), recorded);
  await runtime.ensureSource(agent.handle);
  const source = agentSource(path.join(root, "home"), agent.handle);

  writeFileSync(path.join(source, "README.md"), "agent\n");
  await git(source, ["-c", "user.email=a@b", "-c", "user.name=a", "commit", "--quiet", "-am", "agent"]);
  writeFileSync(path.join(seed, "README.md"), "main\n");
  await git(seed, ["add", "."]);
  await git(seed, ["commit", "--quiet", "-m", "main"]);
  await git(seed, ["push", "--quiet", "origin", "main"]);

  await assert.rejects(runtime.merge(agent), /could not merge .* was aborted/);
  assert.equal(readFileSync(path.join(source, "README.md"), "utf8").replace(/\r\n/g, "\n"), "agent\n");
  assert.equal((await git(source, ["status", "--porcelain"])).trim(), "", "no half-finished merge is left behind");
});

test("only explicitly provisioned local EVAs get independent launchers, with concurrent starts coalesced", async () => {
  const specs = [];
  let exit;
  const runtime = createMagiRuntime({
    checkout: repository, home: repository, workspaceRoot: "/isolated-profile", base: "http://127.0.0.1:42069",
    tools: { ...testRuntime, git: "git", env: { MAGI_HOST: "1", MAGI_ROLE: "shell", MAGI_REVISION_DIR: "/shell-revision", MAGI_CHILD: "/shell-child", MAGI_DESKTOP_UPDATE_JOURNAL: "/desktop-journal", MAGI_DESKTOP_UPDATE_ID: "transaction" } },
    run: async () => "", managed: false,
    startProcess: async (spec) => {
      specs.push(spec);
      return { stop() {}, onExit(callback) { exit = callback; } };
    },
  });
  const local = { handle: EVA_MAGI_HANDLE, local: true, port: 45555, url: "http://127.0.0.1:45555" };
  await Promise.all([runtime.startLocal(local), runtime.startLocal(local)]);
  assert.equal(specs.length, 1);
  assert.equal(specs[0].entry, path.join(repository, "apps/eva/eva.mjs"));
  assert.deepEqual(specs[0].args, ["--role", "agent", EVA_MAGI_HANDLE]);
  assert.equal(specs[0].env.MAGI_HOST, "1");
  assert.equal(specs[0].env.MAGI_HOME, "/isolated-profile");
  assert.equal(specs[0].env.MAGI_CHILD, undefined);
  assert.equal(specs[0].env.MAGI_REVISION_DIR, undefined);
  assert.equal(specs[0].env.MAGI_DESKTOP_UPDATE_JOURNAL, undefined);
  assert.equal(specs[0].env.MAGI_DESKTOP_UPDATE_ID, undefined);
  assert.equal(specs[0].env.MAGI_ASP_TOKEN, undefined);
  assert.equal(specs[0].env.MAGI_PEER_PORT, "45555");
  assert.equal(specs[0].env.MAGI_SERVER, "1");
  assert.deepEqual(await runtime.startLocal(local), { handle: EVA_MAGI_HANDLE, started: false });
  await assert.rejects(runtime.startLocal({ handle: "@external.magi", token: "external" }), /Only explicitly provisioned/);
  exit(0, null);
  assert.deepEqual(runtime.running(), []);
});

test("local launcher errors fail startup and leave no running record", async () => {
  let launches = 0;
  const runtime = createMagiRuntime({
    checkout: repository, home: repository, base: "http://127.0.0.1:9", managed: false,
    tools: { ...testRuntime, env: {} }, run: async () => "",
    spawnProcess() {
      launches += 1;
      const child = new EventEmitter();
      child.stdout = new PassThrough(); child.stdin = new PassThrough();
      child.kill = () => true;
      queueMicrotask(() => child.emit("error", new Error("synthetic launcher failure")));
      return child;
    },
  });
  for (let attempt = 0; attempt < 2; attempt++) {
    await assert.rejects(runtime.startLocal({ handle: EVA_MAGI_HANDLE, local: true, port: 45556, url: "http://127.0.0.1:45556" }), /synthetic launcher failure/);
    assert.deepEqual(runtime.running(), []);
  }
  assert.equal(launches, 2, "a failed start does not retain an in-flight request");
});

test("an operator stop remains running until the owned launcher confirms exit", async () => {
  const exits: Array<() => void> = [];
  let stops = 0;
  const runtime = createMagiRuntime({ checkout: repository, home: repository, base: "http://127.0.0.1:9", managed: false,
    tools: { ...testRuntime, git: "git", env: {} }, run: async () => "",
    startProcess: async () => ({ stop() { stops++; }, onExit(callback) { exits.push(callback); } }),
  });
  await runtime.startLocal({ handle: EVA_MAGI_HANDLE, local: true, port: 45557, url: "http://127.0.0.1:45557" });
  const stopped = runtime.stopLocal(EVA_MAGI_HANDLE);
  assert.equal(stops, 1);
  assert.deepEqual(runtime.running(), [EVA_MAGI_HANDLE]);
  for (const exit of exits) exit();
  assert.equal(await stopped, true);
  assert.deepEqual(runtime.running(), []);
});

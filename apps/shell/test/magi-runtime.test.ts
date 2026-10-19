import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { EventEmitter } from "node:events";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

import { ASP_MAGI_HANDLE, EVA_MAGI_HANDLE, SHELL_MAGI_HANDLE } from "@magi/bus";
import { agentBranch, agentSource, createMagiRuntime } from "@magi/webapp/magi-runtime.js";

const exec = promisify(execFile);
const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const agent = { handle: "@eva-002.magi", token: "tok" };

/** Resolve an explicit MAGI-owned Node/npm pair, never a system executable. */
function projectRuntime() {
  const shell = path.join(repository, "apps", "shell", "runtime");
  const installed = "/Applications/MAGI.app/Contents/Resources/runtime";
  const nodeName = process.platform === "win32" ? "node.exe" : "node";
  for (const root of [process.env.MAGI_TEST_RUNTIME ?? "", shell, installed]) {
    const node = path.join(root, "bin", nodeName);
    const npm = path.join(root, "npm", "node_modules", "npm", "bin", "npm-cli.js");
    if (root !== "" && existsSync(node) && existsSync(npm)) return { node, npm };
  }
  return null;
}

const installedRuntime = projectRuntime();
// Unit tests replace command execution; these paths merely satisfy the same
// explicit-runtime validation used in production.
const testRuntime = installedRuntime ?? { node: process.execPath, npm: fileURLToPath(import.meta.url) };

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
  assert.equal(source, path.join(home, ".magi", "eva-002", "MAGI"));
  assert.equal(existsSync(path.join(source, ".git")), true, "the clone has its own .git directory");
  assert.equal((await git(source, ["rev-parse", "--abbrev-ref", "HEAD"])).trim(), agentBranch(agent.handle));
  assert.equal((await git(source, ["rev-parse", "--is-inside-work-tree"])).trim(), "true");
  assert.equal((await git(origin, ["branch", "--list", "magi/eva-002"])).trim(), "");

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
    { handle: ASP_MAGI_HANDLE, token: "tok-1" },
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
  assert.equal(runtime.stop(ASP_MAGI_HANDLE), false);
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

for (const autocrlf of [false, true]) {
test(`merging advances a MAGI branch and restarts it (autocrlf=${autocrlf})`, async (t) => {
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

}

test("the shell refuses to boot another node's process", async () => {
  const runtime = createMagiRuntime({
    checkout: repository,
    home: repository,
    base: "http://127.0.0.1:9",
    tools: { git: "git", env: process.env, node: process.execPath, npm: "npm" },
    run: async () => "",
    spawnProcess: () => { throw new Error("shell must not spawn a node"); },
    managed: false,
    log: () => {},
  });
  await assert.rejects(runtime.start(agent), /eva --role agent/);
});

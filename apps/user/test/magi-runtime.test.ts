import assert from "node:assert/strict";
import { execFile, spawn } from "node:child_process";
import { EventEmitter } from "node:events";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

import { agentBranch, agentSource, createMagiRuntime } from "../main/magi-runtime.ts";

const exec = promisify(execFile);
const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const agent = { handle: "@eva-000.magi", token: "tok" };

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

async function makeCheckout(root) {
  const checkout = path.join(root, "checkout");
  await git(root, ["init", "--quiet", checkout]);
  writeFileSync(path.join(checkout, "README.md"), "one\n");
  await git(checkout, ["add", "."]);
  await git(checkout, ["-c", "user.email=a@b", "-c", "user.name=a", "commit", "--quiet", "-m", "one"]);
  return checkout;
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

function runtimeWith(checkout, home, recorded, log = () => {}) {
  return createMagiRuntime({
    checkout,
    home,
    base: "http://127.0.0.1:42069",
    tools: { git: "git", env: process.env, ...testRuntime },
    run: recorded.run,
    spawnProcess: recorded.spawnProcess,
    useWorktrees: true,
    log,
  });
}

test("every MAGI gets its own branch checked out inside its workspace", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-source-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const checkout = await makeCheckout(root);
  const home = path.join(root, "home");
  const recorded = recorder();
  const runtime = runtimeWith(checkout, home, recorded);

  assert.deepEqual(await runtime.startAll([agent]), { started: [agent.handle], failed: [] });

  const source = agentSource(home, agent.handle);
  assert.equal(source, path.join(home, ".magi", "eva-000", "MAGI"));
  assert.equal(existsSync(path.join(source, ".git")), true, "the worktree has its own .git pointer");
  assert.equal((await git(source, ["rev-parse", "--abbrev-ref", "HEAD"])).trim(), agentBranch(agent.handle));
  assert.equal((await git(checkout, ["branch", "--list", "magi/eva-000"])).trim().length > 0, true);

  // Dependencies belong to that checkout, and the process runs from it.
  assert.deepEqual(recorded.npm, [
    // `--ignore-scripts` keeps npm from compiling better-sqlite3, which is prebuilt.
    { args: ["ci", "--ignore-scripts"], cwd: source },
    { args: ["run", "build"], cwd: source },
  ]);
  assert.equal(recorded.spawns.length, 1);
  assert.deepEqual(recorded.spawns[0].args, [
    "dist/eva.js", agent.handle, "http://127.0.0.1:42069", "tok",
  ]);
  assert.equal(recorded.spawns[0].options.cwd, path.join(source, "apps", "eva"));

  // Starting a MAGI that already runs changes nothing, and nothing restarts it
  // on its own.
  assert.deepEqual(await runtime.start(agent), { handle: agent.handle, started: false });
  assert.equal(recorded.spawns.length, 1);
  assert.deepEqual(runtime.running(), [agent.handle]);
  assert.deepEqual(await runtime.restart(agent), { handle: agent.handle, started: true });
  assert.equal(recorded.spawns.length, 2);
  assert.deepEqual(runtime.stopAll(), { stopped: 1 });
  assert.deepEqual(runtime.running(), []);

  // A MAGI that exits on its own is not resurrected behind the operator's back.
  assert.deepEqual(await runtime.start(agent), { handle: agent.handle, started: true });
  assert.equal(recorded.spawns.length, 3);
  recorded.spawns[2].child.emit("exit");
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(recorded.spawns.length, 3, "a MAGI that exited stays down");
  assert.deepEqual(runtime.running(), []);
});

test("a checkout that cannot be branched falls back to the shared sources", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-fallback-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const checkout = await makeCheckout(root);
  const logs = [];
  const recorded = recorder();
  const runtime = createMagiRuntime({
    checkout,
    home: path.join(root, "home"),
    base: "http://127.0.0.1:42069",
    tools: { git: "git", env: process.env, ...testRuntime },
    run: async (binary) => {
      if (binary === testRuntime.node) return "";
      throw new Error("git is unavailable");
    },
    spawnProcess: recorded.spawnProcess,
    useWorktrees: true,
    log: (line) => logs.push(line),
  });

  assert.deepEqual(await runtime.start(agent), { handle: agent.handle, started: true });
  assert.equal(recorded.spawns[0].options.cwd, path.join(checkout, "apps", "eva"));
  assert.equal(existsSync(agentSource(path.join(root, "home"), agent.handle)), false);
  assert.equal(logs.some((line) => line.includes("running it from")), true);
});

test("merging the checkout branch advances a MAGI branch and restarts it", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-merge-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const checkout = await makeCheckout(root);
  const recorded = recorder();
  const runtime = runtimeWith(checkout, path.join(root, "home"), recorded);
  await runtime.start(agent);
  const source = agentSource(path.join(root, "home"), agent.handle);

  const branch = (await git(checkout, ["rev-parse", "--abbrev-ref", "HEAD"])).trim();
  writeFileSync(path.join(checkout, "README.md"), "two\n");
  await git(checkout, ["add", "."]);
  await git(checkout, ["-c", "user.email=a@b", "-c", "user.name=a", "commit", "--quiet", "-m", "two"]);

  const merged = await runtime.merge(agent);
  assert.deepEqual(merged, { handle: agent.handle, merged: true, from: branch });
  assert.equal(readFileSync(path.join(source, "README.md"), "utf8"), "two\n");
  assert.equal(recorded.spawns.length, 2, "a MAGI that moved is restarted on the merged source");

  // Nothing new to merge: no second restart.
  assert.deepEqual(await runtime.merge(agent), { handle: agent.handle, merged: false, from: branch });
  assert.equal(recorded.spawns.length, 2);
});

test("a conflicting merge is aborted and reported", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-conflict-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const checkout = await makeCheckout(root);
  const recorded = recorder();
  const runtime = runtimeWith(checkout, path.join(root, "home"), recorded);
  await runtime.start(agent);
  const source = agentSource(path.join(root, "home"), agent.handle);

  // The agent evolves its own branch, the checkout moves the same file.
  writeFileSync(path.join(source, "README.md"), "agent\n");
  await git(source, ["-c", "user.email=a@b", "-c", "user.name=a", "commit", "--quiet", "-am", "agent"]);
  writeFileSync(path.join(checkout, "README.md"), "main\n");
  await git(checkout, ["add", "."]);
  await git(checkout, ["-c", "user.email=a@b", "-c", "user.name=a", "commit", "--quiet", "-m", "main"]);

  await assert.rejects(runtime.merge(agent), /could not merge .* was aborted/);
  assert.equal(readFileSync(path.join(source, "README.md"), "utf8"), "agent\n");
  assert.equal((await git(source, ["status", "--porcelain"])).trim(), "", "no half-finished merge is left behind");
});

test("rebuilding installs and builds in the MAGI's own checkout", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-rebuild-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const checkout = await makeCheckout(root);
  const recorded = recorder();
  const runtime = runtimeWith(checkout, path.join(root, "home"), recorded);
  await runtime.start(agent);
  const source = agentSource(path.join(root, "home"), agent.handle);

  assert.deepEqual(await runtime.rebuild(agent), { handle: agent.handle, rebuilt: true, started: true });
  // The first install belongs to the first start; the rebuild installs again
  // and then builds, all inside that MAGI's own checkout.
  assert.deepEqual(
    recorded.npm.map((call) => (call.args[0] === "ci" ? "install" : call.args[1])),
    ["install", "build", "install", "build"],
  );
  assert.equal(recorded.npm.every((call) => call.cwd === source), true);
  assert.equal(recorded.spawns.length, 2);
});

test("a real MAGI boots from its own checkout", { timeout: 120_000 }, async (t) => {
  if (process.env.MAGI_SKIP_LIVE_INTEGRATION === "1") {
    t.skip("live MAGI-boot integration test skipped: MAGI_SKIP_LIVE_INTEGRATION=1");
    return;
  }
  if (installedRuntime === null) {
    t.skip("MAGI's bundled Node/npm runtime is not installed: run npm ci in apps/shell/, then node apps/shell/scripts/prepare-runtime.mjs");
    return;
  }
  // A checkout out of Git carries no lockfile until someone installs the
  // workspace, and nothing can be installed from one that has none.
  if (!existsSync(path.join(repository, "package-lock.json"))) {
    t.skip("the MAGI workspace is not installed: run npm install at the repository root");
    return;
  }
  const root = mkdtempSync(path.join(tmpdir(), "magi-live-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const checkout = path.join(root, "checkout");
  // `--local` hard-links Git objects, which macOS sandboxed temp directories
  // reject. `--no-local` still tests a real clone without that filesystem tie.
  await git(root, ["clone", "--quiet", "--no-local", repository, checkout]);
  const home = path.join(root, "home");
  // `homedir()` follows HOME on POSIX and USERPROFILE on Windows. Keep the
  // MAGI workspace inside the temp root on either GitHub Actions runner.
  const homeVariable = process.platform === "win32" ? "USERPROFILE" : "HOME";
  const previousHome = process.env[homeVariable];
  process.env[homeVariable] = home;
  t.after(() => {
    if (previousHome === undefined) delete process.env[homeVariable];
    else process.env[homeVariable] = previousHome;
  });
  const childLogs: string[] = [];
  const runtimeEnv = {
    ...process.env,
    PATH: [path.dirname(installedRuntime.node), process.env.PATH ?? ""].join(path.delimiter),
  };
  const runtime = createMagiRuntime({
    checkout,
    home,
    // Nothing listens there: this test only checks that the agent boots.
    base: "http://127.0.0.1:9",
    tools: { git: "git", env: runtimeEnv, ...installedRuntime },
    run: async (binary, args, options) => {
      try {
        const result = await exec(binary, args, {
          cwd: options.cwd,
          env: { ...process.env, ...options.env },
          maxBuffer: 8 * 1024 * 1024,
        });
        return `${result.stdout}`;
      } catch (error) {
        throw new Error(`${options.description} (${error.code ?? "?"}): ${error.stderr ?? ""}`);
      }
    },
    // A real process: this test is about a MAGI actually booting.
    spawnProcess: (binary, args, options) =>
      spawn(binary, args, {
        cwd: options.cwd,
        env: options.env,
        stdio: ["ignore", "ignore", "pipe"],
        detached: true,
        windowsHide: true,
      }),
    useWorktrees: true,
    log: (line) => childLogs.push(line),
  });
  t.after(() => runtime.stopAll());

  assert.deepEqual(await runtime.start(agent), { handle: agent.handle, started: true });
  const source = agentSource(home, agent.handle);
  assert.equal(existsSync(path.join(source, "node_modules")), true);
  const workspace = path.join(home, ".magi", "eva-000", "memories", "books.db");
  const deadline = Date.now() + 30_000;
  while (!existsSync(workspace) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  assert.equal(
    existsSync(workspace),
    true,
    `the MAGI booted far enough to create its workspace${childLogs.length ? `: ${childLogs.join(" | ")}` : ""}`,
  );
});

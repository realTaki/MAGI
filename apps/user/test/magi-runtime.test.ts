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

  assert.deepEqual(await runtime.startAll([agent]), { started: [agent.handle], failed: [] });

  const source = agentSource(home, agent.handle);
  assert.equal(source, path.join(home, ".magi", "eva-002", "MAGI"));
  assert.equal(existsSync(path.join(source, ".git")), true, "the clone has its own .git directory");
  assert.equal((await git(source, ["rev-parse", "--abbrev-ref", "HEAD"])).trim(), agentBranch(agent.handle));
  assert.equal((await git(source, ["rev-parse", "--is-inside-work-tree"])).trim(), "true");
  assert.equal((await git(origin, ["branch", "--list", "magi/eva-002"])).trim(), "");

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

test("in-process hosts are not spawned as eva.js children", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-host-skip-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const { origin } = await makeOrigin(root);
  const home = path.join(root, "home");
  const recorded = recorder();
  const runtime = runtimeWith(origin, home, recorded);

  assert.deepEqual(
    await runtime.startAll([
      { handle: "@eva-000.magi", token: "tok-0" },
      { handle: "@eva-001.magi", token: "tok-1" },
      { handle: "@eva-002.magi", token: "tok-2" },
    ]),
    { started: ["@eva-002.magi"], failed: [] },
  );
  assert.equal(recorded.spawns.length, 1);
  assert.deepEqual(recorded.spawns[0].args, [
    "dist/eva.js", "@eva-002.magi", "http://127.0.0.1:42069", "tok-2",
  ]);
  assert.deepEqual(runtime.running(), ["@eva-002.magi"]);
  assert.deepEqual(await runtime.start({ handle: "@eva-000.magi", token: "tok-0" }), {
    handle: "@eva-000.magi",
    started: false,
  });
  assert.equal(runtime.stop("@eva-001.magi"), false);
  assert.equal(recorded.spawns.length, 1);
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

  await assert.rejects(runtime.start(agent), /Could not clone/);
  assert.equal(recorded.spawns.length, 0);
});

for (const autocrlf of [false, true]) {
test(`merging advances a MAGI branch and restarts it (autocrlf=${autocrlf})`, async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-merge-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const { origin, seed } = await makeOrigin(root, autocrlf);
  const recorded = recorder();
  const runtime = runtimeWith(origin, path.join(root, "home"), recorded);
  await runtime.start(agent);
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
  assert.equal(recorded.spawns.length, 2, "a MAGI that moved is restarted on the merged source");

  // Nothing new to merge: no second restart.
  assert.deepEqual(await runtime.merge(agent), { handle: agent.handle, merged: false, from: "origin/main" });
  assert.equal(recorded.spawns.length, 2);
});

test(`a conflicting merge is aborted and reported (autocrlf=${autocrlf})`, async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-conflict-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const { origin, seed } = await makeOrigin(root, autocrlf);
  const recorded = recorder();
  const runtime = runtimeWith(origin, path.join(root, "home"), recorded);
  await runtime.start(agent);
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

test("rebuilding installs and builds in the MAGI's own checkout", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-rebuild-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const { origin } = await makeOrigin(root);
  const recorded = recorder();
  const runtime = runtimeWith(origin, path.join(root, "home"), recorded);
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

test("a real MAGI boots from its own checkout", { timeout: 300_000 }, async (t) => {
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
  let runtime;
  const children = [];
  t.after(async () => {
    const stopped = children.map((child) => {
      if (!child.pid || child.exitCode !== null || child.signalCode !== null) return Promise.resolve();
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error("Live test MAGI did not exit")), 10_000);
        child.once("exit", () => { clearTimeout(timer); resolve(); });
      });
    });
    runtime?.stopAll();
    await Promise.all(stopped);
    rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  });
  const home = path.join(root, "home");
  const childLogs: string[] = [];
  const runtimeEnv = {
    ...process.env,
    // npm test injects its cache path; keep this integration entirely isolated.
    npm_config_cache: path.join(root, "npm-cache"),
    PATH: [path.dirname(installedRuntime.node), process.env.PATH ?? ""].join(path.delimiter),
  };
  runtime = createMagiRuntime({
    checkout: repository,
    repository,
    home,
    // Nothing listens there: this test only checks that the agent boots.
    base: "http://127.0.0.1:9",
    tools: { git: "git", env: runtimeEnv, ...installedRuntime },
    run: async (binary, args, options) => {
      console.error(`[live integration] ${options.description}`);
      try {
        const result = await exec(binary, args, {
          cwd: options.cwd,
          env: { ...process.env, ...options.env },
          maxBuffer: 8 * 1024 * 1024,
          signal: t.signal,
        });
        console.error(`[live integration] completed: ${options.description}`);
        return `${result.stdout}`;
      } catch (error) {
        throw new Error(`${options.description} (${error.code ?? "?"}): ${error.stderr ?? ""}`);
      }
    },
    // A real process: this test is about a MAGI actually booting.
    spawnProcess: (binary, args, options) => {
      console.error(`[live integration] starting compiled EVA in ${options.cwd}`);
      const child = spawn(binary, [...args, "--workspace", path.join(home, ".magi", "eva-002")], {
        cwd: options.cwd, env: options.env,
        stdio: ["ignore", "ignore", "pipe"], detached: true, windowsHide: true,
      });
      children.push(child);
      return child;
    },
    managed: true,
    log: (line) => childLogs.push(line),
  });

  assert.deepEqual(await runtime.start(agent), { handle: agent.handle, started: true });
  const source = agentSource(home, agent.handle);
  assert.equal(existsSync(path.join(source, "node_modules")), true, `own checkout was not prepared: ${childLogs.join(" | ")}`);
  const workspace = path.join(home, ".magi", "eva-002", "memories", "books.db");
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

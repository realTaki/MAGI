import assert from "node:assert/strict";
import { execFile, spawn } from "node:child_process";
import { EventEmitter } from "node:events";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

import { agentBranch, agentSource, createMagiRuntime } from "../main/magi-runtime.mjs";

const exec = promisify(execFile);
const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

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

/** Records what would run; git is real, bun is never launched. */
function recorder() {
  const bun = [];
  const spawns = [];
  const run = async (binary, args, options) => {
    if (path.basename(binary).startsWith("bun")) {
      bun.push({ args, cwd: options.cwd });
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
  return { run, spawnProcess, bun, spawns };
}

test("every MAGI gets its own branch checked out inside its workspace", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-source-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const checkout = await makeCheckout(root);
  const home = path.join(root, "home");
  const { run, spawnProcess, bun, spawns } = recorder();
  const runtime = createMagiRuntime({
    checkout,
    home,
    base: "http://127.0.0.1:42069",
    tools: { git: "git", env: process.env, bun: "bun" },
    run,
    spawnProcess,
    useWorktrees: true,
  });

  const { started } = await runtime.start([{ handle: "@eva-000.magi", token: "tok" }], { retry: false });
  assert.equal(started, 1);

  const source = agentSource(home, "@eva-000.magi");
  assert.equal(source, path.join(home, ".magi", "eva-000", "MAGI"));
  assert.equal(existsSync(path.join(source, ".git")), true, "the worktree has its own .git pointer");
  assert.equal((await git(source, ["rev-parse", "--abbrev-ref", "HEAD"])).trim(), agentBranch("@eva-000.magi"));
  assert.equal((await git(checkout, ["branch", "--list", "magi/eva-000"])).trim().length > 0, true);

  // Dependencies belong to that checkout, and the process runs from it.
  assert.deepEqual(bun, [{ args: ["install", "--frozen-lockfile"], cwd: path.join(source, "magi") }]);
  assert.equal(spawns.length, 1);
  assert.deepEqual(spawns[0].args, ["run", "start", "--", "@eva-000.magi", "http://127.0.0.1:42069", "tok"]);
  assert.equal(spawns[0].options.cwd, path.join(source, "magi"));

  // Starting an agent that already runs changes nothing.
  assert.deepEqual(await runtime.start([{ handle: "@eva-000.magi", token: "tok" }]), { started: 0 });
  assert.equal(spawns.length, 1);
  assert.deepEqual(runtime.running(), ["@eva-000.magi"]);
  assert.deepEqual(runtime.stop(), { stopped: 1 });
  assert.equal(spawns[0].child.signalCode, "SIGTERM");
});

test("a checkout that cannot be branched falls back to the shared sources", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-fallback-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const checkout = await makeCheckout(root);
  const logs = [];
  const { spawnProcess, spawns } = recorder();
  const runtime = createMagiRuntime({
    checkout,
    home: path.join(root, "home"),
    base: "http://127.0.0.1:42069",
    tools: { git: "git", env: process.env, bun: "bun" },
    run: async (binary) => {
      if (path.basename(binary).startsWith("bun")) return "";
      throw new Error("git is unavailable");
    },
    spawnProcess,
    useWorktrees: true,
    log: (line) => logs.push(line),
  });

  assert.deepEqual(await runtime.start([{ handle: "@eva-001.magi", token: "tok" }], { retry: false }), { started: 1 });
  assert.equal(spawns[0].options.cwd, path.join(checkout, "magi"));
  assert.equal(existsSync(agentSource(path.join(root, "home"), "@eva-001.magi")), false);
  assert.equal(logs.some((line) => line.includes("running it from")), true);
});

test("a stopped society stays stopped until it is asked to start", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-paused-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const checkout = await makeCheckout(root);
  const { run, spawnProcess, spawns } = recorder();
  const runtime = createMagiRuntime({
    checkout,
    home: path.join(root, "home"),
    base: "http://127.0.0.1:42069",
    tools: { git: "git", env: process.env, bun: "bun" },
    run,
    spawnProcess,
    useWorktrees: true,
  });
  const roster = [{ handle: "@eva-000.magi", token: "tok" }];

  runtime.stop();
  assert.deepEqual(await runtime.start(roster), { started: 0 }, "supervision respects a stop");
  assert.deepEqual(await runtime.start(roster, { retry: false }), { started: 0 });
  runtime.resume();
  assert.deepEqual(await runtime.start(roster), { started: 1 });
  assert.equal(spawns.length, 1);
});

test("merging the checkout branch advances an agent branch and restarts it", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-merge-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const checkout = await makeCheckout(root);
  const { run, spawnProcess, spawns } = recorder();
  const runtime = createMagiRuntime({
    checkout,
    home: path.join(root, "home"),
    base: "http://127.0.0.1:42069",
    tools: { git: "git", env: process.env, bun: "bun" },
    run,
    spawnProcess,
    useWorktrees: true,
  });
  const roster = [{ handle: "@eva-000.magi", token: "tok" }];
  await runtime.start(roster, { retry: false });
  const source = agentSource(path.join(root, "home"), "@eva-000.magi");

  const branch = (await git(checkout, ["rev-parse", "--abbrev-ref", "HEAD"])).trim();
  writeFileSync(path.join(checkout, "README.md"), "two\n");
  await git(checkout, ["add", "."]);
  await git(checkout, ["-c", "user.email=a@b", "-c", "user.name=a", "commit", "--quiet", "-m", "two"]);

  const merged = await runtime.merge(roster);
  assert.deepEqual(merged.merged, ["@eva-000.magi"]);
  assert.deepEqual(merged.failed, []);
  assert.equal(merged.from, branch);
  assert.equal(readFileSync(path.join(source, "README.md"), "utf8"), "two\n");
  assert.equal(spawns.length, 2, "an agent that moved is restarted on the merged source");

  // Nothing new to merge: no second restart.
  assert.deepEqual((await runtime.merge(roster)).merged, ["@eva-000.magi"]);
  assert.equal(spawns.length, 2);
});

test("a conflicting merge is aborted and reported", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-conflict-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const checkout = await makeCheckout(root);
  const { run, spawnProcess } = recorder();
  const runtime = createMagiRuntime({
    checkout,
    home: path.join(root, "home"),
    base: "http://127.0.0.1:42069",
    tools: { git: "git", env: process.env, bun: "bun" },
    run,
    spawnProcess,
    useWorktrees: true,
  });
  const roster = [{ handle: "@eva-000.magi", token: "tok" }];
  await runtime.start(roster, { retry: false });
  const source = agentSource(path.join(root, "home"), "@eva-000.magi");

  // The agent evolves its own branch, the checkout moves the same file.
  writeFileSync(path.join(source, "README.md"), "agent\n");
  await git(source, ["-c", "user.email=a@b", "-c", "user.name=a", "commit", "--quiet", "-am", "agent"]);
  writeFileSync(path.join(checkout, "README.md"), "main\n");
  await git(checkout, ["add", "."]);
  await git(checkout, ["-c", "user.email=a@b", "-c", "user.name=a", "commit", "--quiet", "-m", "main"]);

  const merged = await runtime.merge(roster);
  assert.deepEqual(merged.merged, []);
  assert.equal(merged.failed.length, 1);
  assert.equal(merged.failed[0].handle, "@eva-000.magi");
  assert.equal(readFileSync(path.join(source, "README.md"), "utf8"), "agent\n");
  assert.equal((await git(source, ["status", "--porcelain"])).trim(), "", "no half-finished merge is left behind");
});

test("a real MAGI boots from its own checkout", { timeout: 120_000 }, async (t) => {
  const bun = process.env.MAGI_BUN ?? "bun";
  try {
    await exec(bun, ["--version"]);
  } catch {
    t.skip("bun is not available");
    return;
  }
  const root = mkdtempSync(path.join(tmpdir(), "magi-live-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const checkout = path.join(root, "checkout");
  await git(root, ["clone", "--quiet", "--local", repository, checkout]);
  const home = path.join(root, "home");
  // The MAGI resolves its workspace from HOME: keep it inside the temp root.
  const previousHome = process.env.HOME;
  process.env.HOME = home;
  t.after(() => {
    if (previousHome === undefined) delete process.env.HOME;
    else process.env.HOME = previousHome;
  });
  const runtime = createMagiRuntime({
    checkout,
    home,
    // Nothing listens there: this test only checks that the agent boots.
    base: "http://127.0.0.1:9",
    tools: { git: "git", env: process.env, bun },
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
  });
  t.after(() => runtime.stop());

  assert.deepEqual(await runtime.start([{ handle: "@eva-000.magi", token: "tok" }], { retry: false }), { started: 1 });
  const source = agentSource(home, "@eva-000.magi");
  assert.equal(existsSync(path.join(source, "magi", "node_modules")), true);
  const workspace = path.join(home, ".magi", "eva-000", "memories", "magi.db");
  const deadline = Date.now() + 30_000;
  while (!existsSync(workspace) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  assert.equal(existsSync(workspace), true, "the MAGI booted far enough to create its workspace");
});

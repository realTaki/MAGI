import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { EventEmitter } from "node:events";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { createLocalApi } from "../main/index.ts";

function git(cwd, args) {
  return execFileSync("git", args, { cwd, encoding: "utf8" });
}

test("retiring a reloaded client backend keeps ASP and MAGI running", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-runtime-lifecycle-"));
  const checkout = path.join(root, "checkout");
  mkdirSync(path.join(checkout, "asp"), { recursive: true });
  writeFileSync(path.join(checkout, "asp", "main.ts"), "");

  const originalFetch = globalThis.fetch;
  let healthy = false;
  globalThis.fetch = async (url, options = {}) => {
    const endpoint = new URL(url).pathname;
    if (endpoint === "/health") {
      return healthy
        ? Response.json({ status: "ok", runtime: "typescript" })
        : Response.json({ status: "starting" }, { status: 503 });
    }
    if (endpoint === "/operator") return Response.json({ token: "operator-token" });
    if (endpoint === "/bots") {
      return Response.json({ bots: [{ handle: "@eva-000.magi", online: true }] });
    }
    if (endpoint === "/settings/provider/legacy") return Response.json(null);
    throw new Error(`unexpected request: ${options.method ?? "GET"} ${url}`);
  };

  const child = new EventEmitter();
  child.stderr = new EventEmitter();
  child.killed = false;
  let signals = [];
  child.kill = (signal) => {
    signals.push(signal);
    child.killed = true;
    return true;
  };
  const spawn = () => {
    queueMicrotask(() => {
      healthy = true;
      child.emit("spawn");
    });
    return child;
  };

  t.after(() => {
    globalThis.fetch = originalFetch;
    rmSync(root, { recursive: true, force: true });
  });

  const api = createLocalApi({
    paths: { home: root, userData: path.join(root, "userData"), checkout },
    repository: "https://github.com/AgenticSocietyLab/MAGI.git",
    tools: { git: "git", env: process.env },
    emit: () => {},
    openExternal: async () => {},
    copy: () => {},
    spawn,
  });

  await api.start();
  api.dispose();
  assert.deepEqual(signals, [], "client/backend reload must not stop ASP");

  api.shutdown();
  assert.deepEqual(signals, ["SIGTERM"], "desktop shutdown stops its ASP child");
});

test("the app refuses to attach to a running legacy Python ASP", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-legacy-asp-"));
  const checkout = path.join(root, "checkout");
  mkdirSync(path.join(checkout, "asp"), { recursive: true });
  writeFileSync(path.join(checkout, "asp", "main.ts"), "");
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (new URL(url).pathname === "/health") return Response.json({ status: "ok" });
    throw new Error(`unexpected request: ${url}`);
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
    rmSync(root, { recursive: true, force: true });
  });
  let spawned = false;
  const api = createLocalApi({
    paths: { home: root, userData: path.join(root, "userData"), checkout },
    repository: "https://github.com/AgenticSocietyLab/MAGI.git",
    tools: { git: "git", env: process.env },
    emit: () => {},
    openExternal: async () => {},
    copy: () => {},
    spawn() { spawned = true; throw new Error("must not spawn on an occupied port"); },
  });
  await assert.rejects(api.start(), /older ASP is already listening/);
  assert.equal(spawned, false);
  api.dispose();
});

test("the App creates and runs ASP from its own magi/asp worktree", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-runtime-worktrees-"));
  const checkout = path.join(root, "source", "MAGI");
  mkdirSync(path.join(checkout, "asp"), { recursive: true });
  writeFileSync(path.join(checkout, "asp", "package-lock.json"), "{}");
  writeFileSync(path.join(checkout, "asp", "main.ts"), "");
  git(root, ["init", "--quiet", checkout]);
  git(checkout, ["add", "."]);
  git(checkout, ["-c", "user.email=test@example.invalid", "-c", "user.name=MAGI test", "commit", "--quiet", "-m", "initial"]);

  const originalFetch = globalThis.fetch;
  let healthy = false;
  globalThis.fetch = async (url) => {
    if (new URL(url).pathname === "/health") {
      return healthy ? Response.json({ status: "ok", runtime: "typescript" }) : Response.json({}, { status: 503 });
    }
    if (new URL(url).pathname === "/operator") return Response.json({ token: "operator-token" });
    if (new URL(url).pathname === "/bots") return Response.json({ bots: [{ handle: "@eva-000.magi", online: true }] });
    if (new URL(url).pathname === "/agents") return Response.json({ agents: [] });
    if (new URL(url).pathname === "/settings/provider/legacy") return Response.json(null);
    throw new Error(`unexpected request: ${url}`);
  };
  const child = new EventEmitter();
  child.stderr = new EventEmitter();
  child.kill = () => true;
  let spawnedCwd = "";
  const api = createLocalApi({
    paths: { home: root, userData: path.join(root, "userData"), checkout },
    repository: "https://github.com/AgenticSocietyLab/MAGI.git",
    tools: { git: "git", env: process.env }, emit: () => {}, openExternal: async () => {}, copy: () => {},
    managed: true,
    spawn(_binary, _args, options) {
      spawnedCwd = options.cwd;
      queueMicrotask(() => { healthy = true; child.emit("spawn"); });
      return child;
    },
  });
  t.after(() => { globalThis.fetch = originalFetch; api.dispose(); rmSync(root, { recursive: true, force: true }); });

  await api.start();
  assert.equal(spawnedCwd, path.join(root, ".magi", "asp", "MAGI", "asp"));
  assert.notEqual(spawnedCwd, path.join(checkout, "asp"));
  assert.match(git(checkout, ["branch", "--list", "magi/asp"]), /magi\/asp/);
});

test("a MAGI's runtime is driven from its own profile methods", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-runtime-controls-"));
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const endpoint = new URL(url).pathname;
    calls.push(`${options.method ?? "GET"} ${endpoint}`);
    if (endpoint === "/health") return Response.json({ status: "ok", runtime: "typescript" });
    if (endpoint === "/operator") return Response.json({ token: "operator-token" });
    if (endpoint === "/bots") return Response.json({ bots: [{ handle: "@eva-000.magi", online: true }, { handle: "@eva-001.magi", online: false }] });
    if (endpoint === "/agents") {
      return Response.json({
        agents: [
          { handle: "@eva-000.magi", token: "tok-0", name: "eva-000", managed: true, online: true },
          { handle: "@eva-001.magi", token: "tok-1", name: "eva-001", managed: true, online: false },
        ],
      });
    }
    throw new Error(`unexpected request: ${endpoint}`);
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
    rmSync(root, { recursive: true, force: true });
  });
  // An unmanaged checkout: this test is about the bridge, not about worktrees.
  const api = createLocalApi({
    paths: { home: root, userData: path.join(root, "userData"), checkout: root },
    repository: "https://github.com/AgenticSocietyLab/MAGI.git",
    tools: { git: "unused", env: process.env, node: process.execPath, npm: fileURLToPath(import.meta.url) },
    emit: () => {}, openExternal: async () => {}, copy: () => {},
  });
  assert.deepEqual(await api["runtime.status"](), {
    asp: "ready", owned: false, magiOnline: 1, canInstallShellUpdate: false,
  });
  assert.deepEqual(await api["magi.info"]({ handle: "@eva-000.magi" }), {
    handle: "@eva-000.magi",
    online: true,
    running: false,
    branch: "",
    source: path.join(root, "magi"),
  });
  // Starting and stopping MAGI is this backend's job, not ASP's API.
  assert.deepEqual(await api["magi.stop"]({ handle: "@eva-000.magi" }), { handle: "@eva-000.magi", stopped: false });
  await assert.rejects(api["magi.start"]({ handle: "@nobody.magi" }), /Unknown MAGI/);
  await assert.rejects(api["magi.start"]({}), /handle is required/);
  await assert.rejects(api["runtime.buildInstaller"](), /not managed/);
  await assert.rejects(api["runtime.buildAndInstallInstaller"](), /not managed/);
  assert.equal(calls.includes("POST /runtime/magi/stop"), false);
  assert.ok(calls.includes("GET /agents"));
  api.dispose();
});

test("the bulk MAGI actions sweep the roster and report what failed", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-runtime-bulk-"));
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    const endpoint = new URL(url).pathname;
    if (endpoint === "/health") return Response.json({ status: "ok", runtime: "typescript" });
    if (endpoint === "/operator") return Response.json({ token: "operator-token" });
    if (endpoint === "/bots") return Response.json({ bots: [] });
    if (endpoint === "/agents") {
      return Response.json({
        agents: [
          { handle: "@eva-000.magi", token: "tok-0", name: "eva-000", managed: true },
          { handle: "@eva-001.magi", token: "tok-1", name: "eva-001", managed: true },
          // A developer's own checkout is not part of the society a sweep drives.
          { handle: "@plain.magi", token: "tok-2", name: "plain", managed: false },
        ],
      });
    }
    throw new Error(`unexpected request: ${endpoint}`);
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
    rmSync(root, { recursive: true, force: true });
  });

  const api = createLocalApi({
    paths: { home: root, userData: path.join(root, "userData"), checkout: root },
    repository: "https://github.com/AgenticSocietyLab/MAGI.git",
    tools: { git: "unused", env: process.env, node: process.execPath, npm: fileURLToPath(import.meta.url) },
    emit: () => {}, openExternal: async () => {}, copy: () => {},
    spawn(_binary, args) {
      if (args.includes("@eva-001.magi")) throw new Error("no space left");
      const child = new EventEmitter();
      child.stderr = new EventEmitter();
      child.kill = () => true;
      return child;
    },
  });

  // One MAGI failing must not hide the others: the answer names both sides.
  assert.deepEqual(await api["magi.startAll"](), {
    started: ["@eva-000.magi"],
    failed: [{ handle: "@eva-001.magi", detail: "no space left" }],
  });
  assert.deepEqual(await api["magi.stopAll"](), { stopped: 1 });
  await assert.rejects(api["magi.rebuildAll"](), /not managed/);
  api.dispose();
});

test("syncing a module merges the source branch into its worktree", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-runtime-sync-"));
  const checkout = path.join(root, "MAGI");
  mkdirSync(path.join(checkout, "asp"), { recursive: true });
  mkdirSync(path.join(checkout, "app"), { recursive: true });
  writeFileSync(path.join(checkout, "asp", "main.ts"), "");
  writeFileSync(path.join(checkout, "app", "index.html"), "one");
  const commit = (cwd, message) =>
    git(cwd, ["-c", "user.email=test@example.invalid", "-c", "user.name=MAGI test", "commit", "--quiet", "-m", message]);
  git(checkout, ["init", "--quiet", "-b", "main"]);
  git(checkout, ["add", "."]);
  commit(checkout, "initial");
  const appCheckout = path.join(root, ".magi", "app", "MAGI");
  git(checkout, ["worktree", "add", "-b", "magi/app", appCheckout, "HEAD"]);

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    const endpoint = new URL(url).pathname;
    if (endpoint === "/health") return Response.json({ status: "ok", runtime: "typescript" });
    if (endpoint === "/operator") return Response.json({ token: "operator-token" });
    if (endpoint === "/bots") return Response.json({ bots: [] });
    if (endpoint === "/agents") return Response.json({ agents: [] });
    throw new Error(`unexpected request: ${endpoint}`);
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
    rmSync(root, { recursive: true, force: true });
  });

  const api = createLocalApi({
    paths: { home: root, userData: path.join(root, "userData"), checkout, appCheckout },
    repository: "https://github.com/AgenticSocietyLab/MAGI.git",
    tools: { git: "git", env: process.env },
    emit: () => {}, openExternal: async () => {}, copy: () => {},
    managed: true,
    spawn() { throw new Error("a sync must not start anything"); },
  });

  // Each module is synced by its own button; nothing is swept in one go.
  assert.deepEqual(await api["runtime.syncApp"](), {
    synced: [{ module: "app", merged: false }],
    failed: [],
  });
  // The ASP worktree is created on demand, the first time it is asked for.
  assert.deepEqual(await api["runtime.syncAsp"](), {
    synced: [{ module: "asp", merged: false }],
    failed: [],
  });
  assert.deepEqual(await api["magi.syncAll"](), { synced: [], failed: [] });

  // A commit in the source reaches a worktree by merging, without a rebuild.
  writeFileSync(path.join(checkout, "app", "index.html"), "two");
  git(checkout, ["add", "."]);
  commit(checkout, "second");
  assert.deepEqual(await api["runtime.syncApp"](), {
    synced: [{ module: "app", merged: true }],
    failed: [],
  });
  assert.deepEqual(await api["runtime.syncAsp"](), {
    synced: [{ module: "asp", merged: true }],
    failed: [],
  });
  assert.equal(readFileSync(path.join(appCheckout, "app", "index.html"), "utf8"), "two");

  // A worktree that has diverged is reported, not left half-merged.
  writeFileSync(path.join(appCheckout, "app", "index.html"), "worktree side");
  git(appCheckout, ["add", "."]);
  commit(appCheckout, "worktree side");
  writeFileSync(path.join(checkout, "app", "index.html"), "source side");
  git(checkout, ["add", "."]);
  commit(checkout, "source side");
  const conflicted = await api["runtime.syncApp"]();
  assert.deepEqual(conflicted.synced, []);
  assert.deepEqual(conflicted.failed.map((row) => row.module), ["app"]);
  assert.match(conflicted.failed[0].detail, /merge was aborted/);
  assert.equal(git(appCheckout, ["status", "--porcelain"]).trim(), "");

  // A checkout with no remote says so instead of guessing what to fetch.
  await assert.rejects(api["source.update"](), /no origin remote/);
  api.dispose();
});

test("updating the source checkout brings the remote into it", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-runtime-pull-"));
  const checkout = path.join(root, "MAGI");
  mkdirSync(path.join(checkout, "asp"), { recursive: true });
  writeFileSync(path.join(checkout, "asp", "main.ts"), "// old");
  const commit = (cwd, message) =>
    git(cwd, ["-c", "user.email=test@example.invalid", "-c", "user.name=MAGI test", "commit", "--quiet", "-m", message]);
  git(checkout, ["init", "--quiet", "-b", "main"]);
  git(checkout, ["add", "."]);
  commit(checkout, "initial");

  // Somebody else's commit lands on the remote this checkout was cloned from.
  const origin = path.join(root, "origin.git");
  git(root, ["init", "--quiet", "--bare", origin]);
  git(origin, ["symbolic-ref", "HEAD", "refs/heads/main"]);
  git(checkout, ["remote", "add", "origin", origin]);
  git(checkout, ["push", "--quiet", "-u", "origin", "main"]);
  const elsewhere = path.join(root, "elsewhere");
  git(root, ["clone", "--quiet", origin, elsewhere]);
  writeFileSync(path.join(elsewhere, "asp", "main.ts"), "// newer");
  git(elsewhere, ["add", "."]);
  commit(elsewhere, "remote work");
  git(elsewhere, ["push", "--quiet", "origin", "main"]);

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    const endpoint = new URL(url).pathname;
    if (endpoint === "/health") return Response.json({ status: "ok", runtime: "typescript" });
    if (endpoint === "/operator") return Response.json({ token: "operator-token" });
    if (endpoint === "/bots") return Response.json({ bots: [] });
    if (endpoint === "/agents") return Response.json({ agents: [] });
    throw new Error(`unexpected request: ${endpoint}`);
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
    rmSync(root, { recursive: true, force: true });
  });

  const api = createLocalApi({
    paths: { home: root, userData: path.join(root, "userData"), checkout },
    repository: "https://github.com/AgenticSocietyLab/MAGI.git",
    tools: { git: "git", env: process.env },
    emit: () => {}, openExternal: async () => {}, copy: () => {},
    managed: true,
    spawn() { throw new Error("updating the source must not start anything"); },
  });

  assert.deepEqual(await api["source.update"](), {
    branch: "main",
    behind: 1,
    synced: [{ module: "source", merged: true }],
    failed: [],
  });
  assert.equal(readFileSync(path.join(checkout, "asp", "main.ts"), "utf8"), "// newer");
  // Nothing new the second time; the worktrees are only told about it by a sync.
  assert.deepEqual(await api["source.update"](), {
    branch: "main",
    behind: 0,
    synced: [{ module: "source", merged: false }],
    failed: [],
  });
  api.dispose();
});

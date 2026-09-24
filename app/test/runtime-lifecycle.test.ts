import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { EventEmitter } from "node:events";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

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
    tools: { git: "unused", env: process.env },
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

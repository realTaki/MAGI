import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { EventEmitter } from "node:events";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { ASP_MAGI_HANDLE, EVA_MAGI_HANDLE, SHELL_MAGI_HANDLE } from "@magi/bus";
import { createLocalApi } from "../shell.ts";

function git(cwd, args) {
  return execFileSync("git", args, { cwd, encoding: "utf8" });
}

function makeOrigin(root) {
  const origin = path.join(root, "origin.git");
  git(root, ["init", "--quiet", "--bare", origin]);
  const seed = path.join(root, "seed");
  git(root, ["clone", "--quiet", origin, seed]);
  mkdirSync(path.join(seed, "apps", "asp", "dist"), { recursive: true });
  writeFileSync(path.join(seed, "apps", "asp", "package-lock.json"), "{}");
  writeFileSync(path.join(seed, "apps", "asp", "dist", "asp.js"), "");
  git(seed, ["config", "user.email", "test@example.invalid"]);
  git(seed, ["config", "user.name", "MAGI test"]);
  git(seed, ["add", "."]);
  git(seed, ["commit", "--quiet", "-m", "initial"]);
  git(seed, ["branch", "-M", "main"]);
  git(seed, ["push", "--quiet", "-u", "origin", "HEAD:main"]);
  git(origin, ["symbolic-ref", "HEAD", "refs/heads/main"]);
  return { origin, seed };
}

test("retiring the client leaves an ASP the shell did not start", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-runtime-lifecycle-"));
  let api;
  const checkout = path.join(root, "checkout");
  mkdirSync(path.join(checkout, "apps", "asp", "dist"), { recursive: true });
  writeFileSync(path.join(checkout, "apps", "asp", "dist", "asp.js"), "");

  const originalFetch = globalThis.fetch;
  const healthy = true;
  globalThis.fetch = async (url, options = {}) => {
    const endpoint = new URL(url).pathname;
    if (endpoint === "/health") {
      return healthy
        ? Response.json({ status: "ok", runtime: "typescript" })
        : Response.json({ status: "starting" }, { status: 503 });
    }
    if (endpoint === "/operator") return Response.json({ token: "operator-token" });
    if (endpoint === "/agents") {
      return Response.json({
        agents: [
          { handle: SHELL_MAGI_HANDLE, token: "tok-0", name: "shell", managed: true, online: true },
          { handle: ASP_MAGI_HANDLE, token: "tok-1", name: "CASPER", managed: true, online: false },
        ],
      });
    }
    if (endpoint === "/chats") {
      return Response.json({
        chats: [
          { chat_id: "s0", kind: "bot", agents: [SHELL_MAGI_HANDLE] },
          { chat_id: "s1", kind: "bot", agents: [ASP_MAGI_HANDLE] },
          { chat_id: "s2", kind: "bot", agents: [EVA_MAGI_HANDLE] },
        ],
      });
    }
    if (endpoint === "/bots") {
      return Response.json({ bots: [{ handle: SHELL_MAGI_HANDLE, online: true }] });
    }
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

  t.after(async () => {
    await api?.dispose();
    globalThis.fetch = originalFetch;
    rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  });

  api = createLocalApi({
    paths: { home: root, userData: path.join(root, "userData"), checkout },
    repository: "https://github.com/AgenticSocietyLab/MAGI.git",
    tools: { git: "git", env: process.env },
    emit: () => {},
    openExternal: async () => {},
    copy: () => {},
    spawn,
  });

  await api.start();
  await api.dispose();
  assert.deepEqual(signals, [], "reloading the client must not stop ASP");

  await api.shutdown();
  assert.deepEqual(signals, [], "the shell does not own the ASP process");
});

test("the app refuses to attach to a running Python ASP", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-python-asp-"));
  let api;
  const checkout = path.join(root, "checkout");
  mkdirSync(path.join(checkout, "apps", "asp", "dist"), { recursive: true });
  writeFileSync(path.join(checkout, "apps", "asp", "dist", "asp.js"), "");
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (new URL(url).pathname === "/health") return Response.json({ status: "ok" });
    throw new Error(`unexpected request: ${url}`);
  };
  t.after(async () => {
    await api?.dispose();
    globalThis.fetch = originalFetch;
    rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  });
  let spawned = false;
  api = createLocalApi({
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
  await api.dispose();
});

test("the operator process creates and runs ASP from its own magi/asp clone", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-runtime-clones-"));
  let api;
  const { origin, seed } = makeOrigin(root);

  const originalFetch = globalThis.fetch;
  let healthy = false;
  globalThis.fetch = async (url) => {
    if (new URL(url).pathname === "/health") {
      return healthy ? Response.json({ status: "ok", runtime: "typescript" }) : Response.json({}, { status: 503 });
    }
    if (new URL(url).pathname === "/operator") return Response.json({ token: "operator-token" });
    if (new URL(url).pathname === "/bots") return Response.json({ bots: [{ handle: SHELL_MAGI_HANDLE, online: true }] });
    if (new URL(url).pathname === "/agents") {
      return Response.json({
        agents: [
          { handle: SHELL_MAGI_HANDLE, token: "tok-0", name: "shell", managed: true, online: true },
          { handle: ASP_MAGI_HANDLE, token: "tok-1", name: "CASPER", managed: true, online: false },
        ],
      });
    }
    if (new URL(url).pathname === "/chats") {
      return Response.json({
        chats: [
          { chat_id: "s0", kind: "bot", agents: [SHELL_MAGI_HANDLE] },
          { chat_id: "s1", kind: "bot", agents: [ASP_MAGI_HANDLE] },
          { chat_id: "s2", kind: "bot", agents: [EVA_MAGI_HANDLE] },
        ],
      });
    }
    throw new Error(`unexpected request: ${url}`);
  };
  const child = new EventEmitter();
  child.stderr = new EventEmitter();
  child.kill = () => true;
  let spawnedCwd = "";
  api = createLocalApi({
    paths: { home: root, userData: path.join(root, "userData"), checkout: seed },
    repository: origin,
    tools: { git: "git", env: process.env }, emit: () => {}, openExternal: async () => {}, copy: () => {},
    managed: true,
    spawn(_binary, _args, options) {
      spawnedCwd = options.cwd;
      queueMicrotask(() => { healthy = true; child.emit("spawn"); });
      return child;
    },
  });
  t.after(async () => { await api?.dispose(); globalThis.fetch = originalFetch; rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); });

  await assert.rejects(api.start(), /eva --role asp/);
  assert.equal(spawnedCwd, "");
  const aspClone = path.join(root, ".magi", "asp", "MAGI");
  assert.equal(git(aspClone, ["rev-parse", "--abbrev-ref", "HEAD"]).trim(), "magi/asp");
});

test("a MAGI's runtime is driven from its own profile methods", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-runtime-controls-"));
  let api;
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const endpoint = new URL(url).pathname;
    calls.push(`${options.method ?? "GET"} ${endpoint}`);
    if (endpoint === "/health") return Response.json({ status: "ok", runtime: "typescript" });
    if (endpoint === "/operator") return Response.json({ token: "operator-token" });
    if (endpoint === "/bots") return Response.json({ bots: [{ handle: SHELL_MAGI_HANDLE, online: true }, { handle: ASP_MAGI_HANDLE, online: false }] });
    if (endpoint === "/agents") {
      return Response.json({
        agents: [
          { handle: SHELL_MAGI_HANDLE, token: "tok-0", name: "shell", managed: true, online: true },
          { handle: ASP_MAGI_HANDLE, token: "tok-1", name: "eva-001", managed: true, online: false },
        ],
      });
    }
    throw new Error(`unexpected request: ${endpoint}`);
  };
  t.after(async () => {
    await api?.dispose();
    globalThis.fetch = originalFetch;
    rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  });
  api = createLocalApi({
    paths: { home: root, userData: path.join(root, "userData"), checkout: root },
    repository: "https://github.com/AgenticSocietyLab/MAGI.git",
    tools: { git: "unused", env: process.env, node: process.execPath, npm: fileURLToPath(import.meta.url) },
    emit: () => {}, openExternal: async () => {}, copy: () => {},
  });
  assert.deepEqual(await api["runtime.status"](), {
    asp: "ready", owned: false, magiOnline: 1, canInstallShellUpdate: false,
    sourceCheckout: root, runtimeCheckout: root,
  });
  assert.deepEqual(await api["magi.info"]({ handle: SHELL_MAGI_HANDLE }), {
    handle: SHELL_MAGI_HANDLE,
    online: true,
    running: false,
    branch: "",
    source: root,
  });
  assert.deepEqual(await api["magi.stop"]({ handle: SHELL_MAGI_HANDLE }), { handle: SHELL_MAGI_HANDLE, stopped: false });
  await assert.rejects(api["magi.start"]({ handle: "@nobody.magi" }), /Unknown MAGI/);
  await assert.rejects(api["magi.start"]({}), /handle is required/);
  await assert.rejects(api["runtime.buildInstaller"](), /No verified editable MAGI source/);
  await assert.rejects(api["runtime.buildAndInstallInstaller"](), /requires an app-managed installation/);
  assert.equal(calls.includes("POST /runtime/magi/stop"), false);
  assert.ok(calls.includes("GET /agents"));
  await api.dispose();
});

test("ensureDefaultMagis reuses an existing owner 1:1 and does not open a second", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-default-1to1-"));
  let api;
  const checkout = path.join(root, "checkout");
  mkdirSync(path.join(checkout, "apps", "asp", "dist"), { recursive: true });
  writeFileSync(path.join(checkout, "apps", "asp", "dist", "asp.js"), "");
  const originalFetch = globalThis.fetch;
  const createdHandles = [];
  const chats = [
    { chat_id: "old-balthasar", name: "BALTHASAR", kind: "bot", agents: ["@eva-000.magi"], created_at: 0 },
    { chat_id: "casper-1", kind: "bot", agents: [ASP_MAGI_HANDLE], created_at: 1 },
  ];
  globalThis.fetch = async (url, options = {}) => {
    const endpoint = new URL(url).pathname;
    const method = options.method ?? "GET";
    if (endpoint === "/health") return Response.json({ status: "ok", runtime: "typescript" });
    if (endpoint === "/operator") return Response.json({ token: "operator-token" });
    if (endpoint === "/agents") {
      return Response.json({
        agents: [
          { handle: SHELL_MAGI_HANDLE, token: "tok-0", name: "shell", managed: true, online: true },
          { handle: ASP_MAGI_HANDLE, token: "tok-1", name: "CASPER", managed: true, online: true },
        ],
      });
    }
    if (endpoint === "/chats" && method === "GET") return Response.json({ chats });
    if (endpoint === "/chats" && method === "POST") {
      const body = JSON.parse(String(options.body ?? "{}"));
      createdHandles.push(body.handle);
      const chat_id = `new-${body.handle}`;
      chats.push({ chat_id, kind: "bot", agents: [body.handle], created_at: Date.now() });
      return Response.json({ chat_id, kind: "bot", agents: [body.handle] });
    }
    if (endpoint === "/bots") return Response.json({ bots: [] });
    if (method === "PATCH" && endpoint.startsWith("/bots/")) return Response.json({ ok: true });
    if (method === "POST" && endpoint.endsWith("/messages")) return Response.json({ ok: true });
    throw new Error(`unexpected request: ${method} ${url}`);
  };
  t.after(async () => {
    await api?.dispose();
    globalThis.fetch = originalFetch;
    rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  });
  api = createLocalApi({
    paths: { home: root, userData: path.join(root, "userData"), checkout },
    repository: "https://github.com/AgenticSocietyLab/MAGI.git",
    tools: { git: "git", env: process.env },
    emit: () => {},
    openExternal: async () => {},
    copy: () => {},
  });
  await api.start();
  assert.equal(createdHandles.includes(ASP_MAGI_HANDLE), false, "Casper already has a 1:1");
  assert.equal(createdHandles.filter((handle) => handle === ASP_MAGI_HANDLE).length, 0);
  assert.equal(chats.filter((chat) => chat.agents[0] === ASP_MAGI_HANDLE).length, 1);
  assert.equal(createdHandles.includes(SHELL_MAGI_HANDLE), true);
  assert.equal(createdHandles.includes(EVA_MAGI_HANDLE), true);
  assert.equal(chats.filter((chat) => chat.agents[0] === "@eva-000.magi").length, 1, "old Shell identity is retained");
  assert.equal(chats.filter((chat) => chat.agents[0] === SHELL_MAGI_HANDLE).length, 1, "default setup creates the renamed handle separately");
  await api.dispose();
});

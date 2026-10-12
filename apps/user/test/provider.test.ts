import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

import { Bus } from "@magi/bus";
import { createLocalApi } from "../main/index.ts";

/*
 * Business flow: switching the model, desktop side (`ARCHITECTURE.md`, "ASP").
 * The key is kept in the User BUS SettingsBook and reaches the MAGI
 * through ASP; User persists credentials atomically and ASP only relays.
 */

const active = new Map();
async function closeApps(root) {
  for (const api of active.get(root) ?? []) await api.dispose();
  active.delete(root);
}
function app(root, checkout = path.join(root, "checkout")) {
  const api = createLocalApi({
    paths: {
      home: root,
      userData: path.join(root, "userData"),
      checkout,
    },
    repository: "https://github.com/AgenticSocietyLab/MAGI.git",
    tools: { git: "git", env: process.env },
    emit: () => {},
    openExternal: async () => {},
    copy: () => {},
  });
  const instances = active.get(root) ?? [];
  instances.push(api);
  active.set(root, instances);
  return api;
}

test("the app saves the provider key locally and only broadcasts it through ASP", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-provider-test-"));
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ path: new URL(url).pathname, method: options.method ?? "GET", body: options.body });
    if (new URL(url).pathname === "/operator") {
      return Response.json({ token: "operator-token" });
    }
    if (new URL(url).pathname === "/settings/provider/legacy") {
      return Response.json(null);
    }
    if (new URL(url).pathname === "/settings/provider") {
      return Response.json({ ...JSON.parse(options.body), synced: ["@eva-000.magi"], failed: [] });
    }
    throw new Error(`unexpected request: ${url}`);
  };
  t.after(async () => { await closeApps(root); globalThis.fetch = originalFetch; rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); });

  const first = app(root);
  const saved = await first["provider.save"]({ provider: "custom", model: "opus", api_key: "sk-local", base_url: "https://example.com/v1" });
  assert.deepEqual(saved.synced, ["@eva-000.magi"]);
  assert.ok(requests.some((request) => request.method === "PUT" && request.path === "/settings/provider"));
  assert.equal(JSON.parse(requests.find((request) => request.method === "PUT" && request.path === "/settings/provider").body).base_url, "https://example.com/v1");
  const providerFile = path.join(root, ".magi", "user", "provider.json");
  assert.equal(existsSync(providerFile), false, "credentials live in SettingsBook");
  const books = path.join(root, ".magi", "user", "memories", "books.db");
  if (process.platform !== "win32") assert.equal(statSync(books).mode & 0o777, 0o600);
  await first.dispose();
  const second = app(root);
  assert.equal(second["provider.settings"]().api_key, "sk-local");
  assert.equal(second["provider.settings"]().base_url, "https://example.com/v1");
  await second.dispose();
  await first.dispose();
});

test("legacy app state moves to the user directory", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-user-directory-"));
  t.after(async () => { await closeApps(root); rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); });
  const legacy = path.join(root, ".magi", "app");
  mkdirSync(legacy, { recursive: true });
  writeFileSync(path.join(legacy, "provider.json"), JSON.stringify({ api_key: "preserved" }));

  const backend = app(root);
  assert.equal(backend["provider.settings"]().api_key, "preserved");
  assert.equal(existsSync(path.join(root, ".magi", "user", "provider.json")), false);
  assert.equal(existsSync(legacy), false);
  await backend.dispose();
});

test("the app reads the curated provider catalog from pi-ai", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-provider-catalog-"));
  t.after(async () => { await closeApps(root); rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); });
  const checkout = path.resolve(import.meta.dirname, "../../..");
  const backend = app(root, checkout);
  const catalog = await backend["provider.catalog"]();
  assert.deepEqual(Object.keys(catalog), ["openai", "anthropic", "minimax-cn", "minimax-global", "deepseek"]);
  for (const models of Object.values(catalog)) assert.ok(models.length > 0);
  assert.deepEqual(catalog["minimax-cn"].map((model) => model.id), catalog["minimax-global"].map((model) => model.id));
  await backend.dispose();
});

test("the app does not send the key to an older ASP that still persists it", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-provider-old-asp-"));
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, options = {}) => {
    const endpoint = new URL(url).pathname;
    requests.push(`${options.method ?? "GET"} ${endpoint}`);
    if (endpoint === "/operator") return Response.json({ token: "operator-token" });
    if (endpoint === "/settings/provider/legacy") return Response.json({ detail: "not found" }, { status: 404 });
    throw new Error(`unexpected request: ${url}`);
  };
  t.after(async () => { await closeApps(root); globalThis.fetch = originalFetch; rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); });

  const backend = app(root);
  await assert.rejects(backend["provider.save"]({ api_key: "sk-local" }), /Saved in the app, but MAGI sync failed/);
  assert.equal(backend["provider.settings"]().api_key, "sk-local");
  assert.equal(requests.includes("PUT /settings/provider"), false);
  await backend.dispose();
});

test("a provider rejection keeps the app's previous key", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-provider-reject-"));
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    const endpoint = new URL(url).pathname;
    if (endpoint === "/operator") return Response.json({ token: "operator-token" });
    if (endpoint === "/settings/provider/legacy") return Response.json(null);
    if (endpoint === "/settings/provider") {
      const input = JSON.parse(options.body);
      return Response.json(input.api_key === "bad-key"
        ? { ...input, synced: [], failed: [{ handle: "@eva-000.magi", detail: "MAGI rejected provider configuration" }] }
        : { ...input, synced: ["@eva-000.magi"], failed: [] });
    }
    throw new Error(`unexpected request: ${url}`);
  };
  t.after(async () => { await closeApps(root); globalThis.fetch = originalFetch; rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); });

  const backend = app(root);
  await backend["provider.save"]({ provider: "openai", model: "gpt", api_key: "good-key" });
  await assert.rejects(backend["provider.save"]({ provider: "openai", model: "gpt", api_key: "bad-key" }), /previous settings were kept/);
  assert.equal(backend["provider.settings"]().api_key, "good-key");
  await backend.dispose();
});

test("the app copies a legacy ASP key before deleting that copy", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-provider-migrate-"));
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, options = {}) => {
    const endpoint = new URL(url).pathname;
    requests.push(`${options.method ?? "GET"} ${endpoint}`);
    if (endpoint === "/health") return Response.json({ status: "ok", runtime: "typescript" });
    if (endpoint === "/operator") return Response.json({ token: "operator-token" });
    if (endpoint === "/bots") return Response.json({ bots: [{ handle: "@eva-000.magi", online: false }] });
    if (endpoint === "/settings/provider/legacy" && options.method === "DELETE") {
      const check = new Bus("@user.magi", path.join(root, ".magi", "user"));
      try { assert.equal(check.settings.get("provider.api_key"), "old-key"); }
      finally { check.close(); }
      return Response.json({ ok: true });
    }
    if (endpoint === "/settings/provider/legacy") {
      return Response.json({ provider: "openai", model: "gpt", api_key: "old-key" });
    }
    throw new Error(`unexpected request: ${url}`);
  };
  t.after(async () => { await closeApps(root); globalThis.fetch = originalFetch; rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); });

  const backend = app(root);
  await backend.start();
  assert.equal(existsSync(path.join(root, ".magi", "user", "provider.json")), false);
  assert.equal(backend["provider.settings"]().api_key, "old-key");
  assert.ok(requests.includes("DELETE /settings/provider/legacy"));
  await backend.dispose();
});

test("usage reads DeepSeek balance with the locally saved API key", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-provider-usage-"));
  const appData = path.join(root, ".magi", "user");
  mkdirSync(appData, { recursive: true });
  writeFileSync(
    path.join(appData, "provider.json"),
    JSON.stringify({ provider: "deepseek", model: "deepseek-v4-pro", api_key: "local-key" }),
  );
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    assert.equal(String(url), "https://api.deepseek.com/user/balance");
    assert.equal(options.headers.Authorization, "Bearer local-key");
    return Response.json({
      is_available: true,
      balance_infos: [{ currency: "USD", total_balance: "12.34" }],
    });
  };
  t.after(async () => {
    await closeApps(root);
    globalThis.fetch = originalFetch;
    rmSync(root, { recursive: true, force: true });
  });

  const backend = app(root);
  assert.deepEqual(await backend["provider.usage"](), {
    provider: "deepseek",
    status: "available",
    available: true,
    balances: [{ currency: "USD", total: "12.34" }],
    message: "",
  });
  await backend.dispose();
});

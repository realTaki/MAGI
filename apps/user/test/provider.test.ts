import assert from "node:assert/strict";
import { existsSync, mkdtempSync, rmSync, statSync } from "node:fs";
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
  assert.equal(existsSync(path.join(root, ".magi", "user", "provider.json")), false, "credentials live in SettingsBook");
  const books = path.join(root, ".magi", "user", "memories", "books.db");
  if (process.platform !== "win32") assert.equal(statSync(books).mode & 0o777, 0o600);
  await first.dispose();
  const second = app(root);
  assert.equal(second["provider.settings"]().api_key, "sk-local");
  assert.equal(second["provider.settings"]().base_url, "https://example.com/v1");
  await second.dispose();
  await first.dispose();
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

test("a provider rejection keeps the app's previous key", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-provider-reject-"));
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    const endpoint = new URL(url).pathname;
    if (endpoint === "/operator") return Response.json({ token: "operator-token" });
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

test("usage reads DeepSeek balance with the locally saved API key", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-provider-usage-"));
  const appData = path.join(root, ".magi", "user");
  const bus = new Bus("@user.magi", appData);
  try {
    bus.settings.setMany({
      "provider.name": "deepseek",
      "provider.model": "deepseek-v4-pro",
      "provider.api_key": "local-key",
    });
  } finally { bus.close(); }
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

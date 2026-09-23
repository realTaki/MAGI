import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

import { createLocalApi } from "../main/index.mjs";

function app(root) {
  return createLocalApi({
    paths: {
      home: root,
      userData: path.join(root, "userData"),
      checkout: path.join(root, "checkout"),
    },
    repository: "https://github.com/AgenticSocietyLab/MAGI.git",
    tools: { git: "git", env: process.env, python: "python" },
    emit: () => {},
    openExternal: async () => {},
    copy: () => {},
  });
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
  t.after(() => { globalThis.fetch = originalFetch; rmSync(root, { recursive: true, force: true }); });

  const first = app(root);
  const saved = await first["provider.save"]({ provider: "claude", model: "opus", api_key: "sk-local" });
  assert.deepEqual(saved.synced, ["@eva-000.magi"]);
  assert.equal(requests[1].method, "PUT");
  assert.equal(requests[1].path, "/settings/provider");
  const providerFile = path.join(root, ".magi", "app", "provider.json");
  assert.equal(JSON.parse(readFileSync(providerFile, "utf8")).api_key, "sk-local");
  if (process.platform !== "win32") assert.equal(statSync(providerFile).mode & 0o777, 0o600);
  assert.equal(app(root)["provider.settings"]().api_key, "sk-local");
  first.dispose();
});

test("the app copies a legacy ASP key before deleting that copy", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-provider-migrate-"));
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, options = {}) => {
    const endpoint = new URL(url).pathname;
    requests.push(`${options.method ?? "GET"} ${endpoint}`);
    if (endpoint === "/health") return Response.json({ status: "ok" });
    if (endpoint === "/operator") return Response.json({ token: "operator-token" });
    if (endpoint === "/bots") return Response.json({ bots: [{ handle: "@eva-000.magi", online: false }] });
    if (endpoint === "/settings/provider/legacy" && options.method === "DELETE") {
      assert.equal(app(root)["provider.settings"]().api_key, "old-key");
      return Response.json({ ok: true });
    }
    if (endpoint === "/settings/provider/legacy") {
      return Response.json({ provider: "openai", model: "gpt", api_key: "old-key" });
    }
    throw new Error(`unexpected request: ${url}`);
  };
  t.after(() => { globalThis.fetch = originalFetch; rmSync(root, { recursive: true, force: true }); });

  const backend = app(root);
  await backend.start();
  assert.equal(existsSync(path.join(root, ".magi", "app", "provider.json")), true);
  assert.ok(requests.includes("DELETE /settings/provider/legacy"));
  backend.dispose();
});

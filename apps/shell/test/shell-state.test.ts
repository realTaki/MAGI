import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { createLocalApi } from "../shell.ts";

function fixture(t) {
  const home = mkdtempSync(path.join(tmpdir(), "magi-shell-state-"));
  const current = path.join(home, ".magi", "shell");
  const apis = [];
  t.after(async () => { for (const api of apis) await api.dispose(); rmSync(home, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); });
  return { home, current, app() {
    const api = createLocalApi({ paths: { home, checkout: path.join(home, "checkout"), userData: path.join(home, "electron") }, repository: "https://github.com/realTaki/MAGI.git", tools: { git: "git", env: process.env }, emit: () => {}, openExternal: async () => {}, copy: () => {} });
    apis.push(api); return api;
  } };
}

test("cache initialization failure is reported by ready and disposal still closes BUS", async (t) => {
  const f = fixture(t);
  mkdirSync(path.join(f.current, "chat.sqlite"), { recursive: true });
  const api = f.app();
  await assert.rejects(api.ready(), /unable to open|Cannot open|directory/i);
  await api.dispose();
  assert.equal(existsSync(path.join(f.current, "chat.sqlite")), true);
});

test("disposing Shell aborts an in-flight ASP request", async (t) => {
  const f = fixture(t);
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  let aborted = false;
  let began: () => void = () => {};
  const started = new Promise<void>((resolve) => { began = resolve; });
  globalThis.fetch = async (url, options: RequestInit = {}) => {
    const endpoint = new URL(String(url)).pathname;
    if (endpoint === "/health") return Response.json({ status: "ok", runtime: "typescript" });
    if (endpoint === "/operator") return Response.json({ token: "test" });
    if (endpoint === "/bots") {
      began();
      return await new Promise((_resolve, reject) => {
        options.signal?.addEventListener("abort", () => { aborted = true; reject(options.signal?.reason); }, { once: true });
      });
    }
    throw new Error(`Unexpected endpoint ${endpoint}`);
  };
  const api = f.app();
  const pending = api["runtime.status"]().then(
    () => false,
    () => true,
  );
  await started;
  await api.dispose();
  assert.equal(await pending, true);
  assert.equal(aborted, true, "closing Shell cancels its network request");
});

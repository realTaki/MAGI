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

test("Shell status and disposal do not contact a central service", async (t) => {
  const f = fixture(t);
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  let requests = 0;
  globalThis.fetch = async () => { requests++; throw new Error("Unexpected central network request"); };
  const api = f.app();
  await api.ready();
  const status = await api["runtime.status"]();
  assert.equal(status.shell, "ready");
  assert.equal(status.magiOnline, 1);
  await api.dispose();
  await api.dispose();
  assert.equal(requests, 0);
});

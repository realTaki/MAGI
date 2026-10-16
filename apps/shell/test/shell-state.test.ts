import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import Database from "better-sqlite3";
import { Bus, SHELL_MAGI_HANDLE } from "@magi/bus";
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

const keys = { "provider.name": "openai", "provider.model": "gpt", "provider.api_key": "preserved", "provider.base_url": "" };

function seedSettings(directory, settings) {
  const bus = new Bus(SHELL_MAGI_HANDLE, directory);
  try { bus.settings.setMany(settings); } finally { bus.close(); }
}

test("provider write failure rolls back every SettingsBook field", async (t) => {
  const f = fixture(t);
  seedSettings(f.current, keys);
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async (url) => {
    const endpoint = new URL(url).pathname;
    if (endpoint === "/operator") return Response.json({ token: "test" });
    throw new Error("Provider must not broadcast after a write failure");
  };
  const api = f.app(); await api.ready();
  const db = new Database(path.join(f.current, "memories", "books.db"));
  try { db.exec("CREATE TRIGGER reject_provider BEFORE INSERT ON books_settings WHEN NEW.key='provider.model' BEGIN SELECT RAISE(ABORT,'injected provider write failure'); END"); } finally { db.close(); }
  await assert.rejects(api["provider.save"]({ provider: "custom", model: "new-model", api_key: "new-key" }), /injected provider write failure/);
  assert.deepEqual(api["provider.settings"](), { provider: "openai", model: "gpt", api_key: "preserved", base_url: null });
});

test("cache initialization failure is reported by ready and disposal still closes BUS", async (t) => {
  const f = fixture(t);
  mkdirSync(path.join(f.current, "chat.sqlite"), { recursive: true });
  const api = f.app();
  await assert.rejects(api.ready(), /unable to open|Cannot open|directory/i);
  await api.dispose();
  assert.equal(existsSync(path.join(f.current, "chat.sqlite")), true);
});

test("disposing Shell aborts an in-flight provider poll without rescheduling", async (t) => {
  const f = fixture(t); seedSettings(f.current, keys);
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  let bots = 0, aborted = false, began;
  const started = new Promise((resolve) => { began = resolve; });
  globalThis.fetch = async (url, options = {}) => {
    const endpoint = new URL(url).pathname;
    if (endpoint === "/health") return Response.json({ status: "ok", runtime: "typescript" });
    if (endpoint === "/operator") return Response.json({ token: "test" });
    if (endpoint === "/agents") {
      return Response.json({
        agents: [
          { handle: "@eva-000.magi", token: "tok-0", name: "eva-000", managed: true },
          { handle: "@eva-001.magi", token: "tok-1", name: "CASPER", managed: true },
        ],
      });
    }
    if (endpoint === "/chats") {
      return Response.json({
        chat_id: "sess_002",
        agents: ["@eva-002.magi"],
      });
    }
    if (endpoint === "/bots") {
      if (++bots === 1) return Response.json({ bots: [{ handle: "@eva.magi", online: false }] });
      began();
      return await new Promise((_resolve, reject) => {
        options.signal.addEventListener("abort", () => { aborted = true; reject(options.signal.reason); }, { once: true });
      });
    }
    throw new Error(`Unexpected endpoint ${endpoint}`);
  };
  const api = f.app(); await api.start();
  await started;
  await api.dispose();
  assert.equal(aborted, true, "closing Shell cancels its network request");
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(bots, 2, "closed Shell never schedules another recipient poll");
});

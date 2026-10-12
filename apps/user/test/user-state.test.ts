import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import Database from "better-sqlite3";
import { Bus } from "@magi/bus";
import { createLocalApi } from "../main/index.ts";
import { openChatStore } from "../main/chat-store.ts";

function fixture(t) {
  const home = mkdtempSync(path.join(tmpdir(), "magi-user-state-"));
  const current = path.join(home, ".magi", "user");
  const legacy = path.join(home, ".magi", "app");
  const apis = [];
  t.after(async () => { for (const api of apis) await api.dispose(); rmSync(home, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); });
  return { home, current, legacy, app() {
    const api = createLocalApi({ paths: { home, checkout: path.join(home, "checkout"), userData: path.join(home, "electron") }, repository: "https://github.com/realTaki/MAGI.git", tools: { git: "git", env: process.env }, emit: () => {}, openExternal: async () => {}, copy: () => {} });
    apis.push(api); return api;
  } };
}

const keys = { "provider.name": "openai", "provider.model": "gpt", "provider.api_key": "preserved", "provider.base_url": "" };
const legacyProvider = JSON.stringify({ provider: "openai", model: "gpt", api_key: "preserved" });

function seedSettings(directory, settings) {
  const bus = new Bus("@user.magi", directory);
  try { bus.settings.setMany(settings); } finally { bus.close(); }
}

test("old directory moves before BUS creation, preserving keys, directory and cached receipts", async (t) => {
  const f = fixture(t);
  const bus = new Bus("@user.magi", f.legacy);
  bus.settings.set("user-state.sentinel", "do not overwrite");
  const store = openChatStore(path.join(f.legacy, "chat.sqlite"), bus);
  const event = { sequence: 5, event_id: "old", type: "chat.message", payload: { content: "old history" } };
  try {
    store.saveChats([{ chat_id: "old-chat", kind: "group", agents: [] }]);
    store.saveEvents("old-chat", [event]);
    store.queueOutgoingMessage({ id: "pending", chatId: "old-chat", content: "unsent", createdAt: 1 });
  } finally { store.close(); bus.close(); }
  writeFileSync(path.join(f.legacy, "provider.json"), legacyProvider);
  const api = f.app();
  await api.ready();
  assert.equal(existsSync(f.legacy), false);
  assert.equal(api["provider.settings"]().api_key, "preserved");
  assert.deepEqual(await api["chat.listEvents"]("old-chat"), [event]);
  assert.deepEqual(await api["chat.pendingAcks"]("old-chat"), [event]);
  assert.deepEqual(await api["chat.listOutgoingMessages"](), [{ id: "pending", chatId: "old-chat", content: "unsent", createdAt: 1 }]);
  await api.dispose();
  const reopened = f.app();
  await reopened.ready();
  assert.equal(reopened["provider.settings"]().api_key, "preserved");
  assert.equal((await reopened["chat.listChats"]())[0].chat_id, "old-chat");
  const check = new Bus("@user.magi", f.current);
  try { assert.equal(check.settings.get("user-state.sentinel"), "do not overwrite"); } finally { check.close(); }
});

test("two state directories refuse ambiguous migration and preserve both", (t) => {
  const f = fixture(t);
  for (const [directory, text] of [[f.legacy, "old"], [f.current, "current"]]) {
    mkdirSync(directory, { recursive: true }); writeFileSync(path.join(directory, "marker"), text);
  }
  assert.throws(() => f.app(), /Both User state directories exist/);
  assert.equal(readFileSync(path.join(f.legacy, "marker"), "utf8"), "old");
  assert.equal(readFileSync(path.join(f.current, "marker"), "utf8"), "current");
  assert.equal(existsSync(path.join(f.current, "memories", "books.db")), false, "BUS was never opened");
});

test("conflicting provider file cannot overwrite SettingsBook or disappear", (t) => {
  const f = fixture(t);
  seedSettings(f.current, { ...keys, "provider.api_key": "current-key" });
  writeFileSync(path.join(f.current, "provider.json"), legacyProvider);
  assert.throws(() => f.app(), /conflict with SettingsBook/);
  assert.equal(readFileSync(path.join(f.current, "provider.json"), "utf8"), legacyProvider);
  const check = new Bus("@user.magi", f.current);
  try { assert.equal(check.settings.get("provider.api_key"), "current-key"); } finally { check.close(); }
});

test("matching migration retry is idempotent; malformed file remains recoverable", async (t) => {
  const f = fixture(t);
  seedSettings(f.current, keys);
  const file = path.join(f.current, "provider.json");
  writeFileSync(file, "invalid legacy content");
  assert.throws(() => f.app(), /Invalid legacy provider/);
  assert.equal(readFileSync(file, "utf8"), "invalid legacy content");
  writeFileSync(file, legacyProvider);
  const api = f.app(); await api.ready();
  assert.equal(existsSync(file), false);
  assert.equal(api["provider.settings"]().api_key, "preserved");
});

test("provider write failure rolls back every SettingsBook field", async (t) => {
  const f = fixture(t);
  seedSettings(f.current, keys);
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

test("disposing User aborts an in-flight provider poll without rescheduling", async (t) => {
  const f = fixture(t); seedSettings(f.current, keys);
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  let bots = 0, aborted = false, began;
  const started = new Promise((resolve) => { began = resolve; });
  globalThis.fetch = async (url, options = {}) => {
    const endpoint = new URL(url).pathname;
    if (endpoint === "/health") return Response.json({ status: "ok", runtime: "typescript" });
    if (endpoint === "/operator") return Response.json({ token: "test" });
    if (endpoint === "/agents") return Response.json({ agents: [] });
    if (endpoint === "/settings/provider/legacy") return Response.json(null);
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
  assert.equal(aborted, true, "closing User cancels its network request");
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(bots, 2, "closed User never schedules another recipient poll");
});

test("invalid legacy cache record is retained and its failed connection closes", (t) => {
  const f = fixture(t);
  const bus = new Bus("@user.magi", f.current);
  const file = path.join(f.current, "chat.sqlite");
  const legacy = new Database(file);
  try {
    legacy.exec("CREATE TABLE chats (id TEXT PRIMARY KEY, record_json TEXT NOT NULL)");
    legacy.prepare("INSERT INTO chats VALUES (?, ?)").run("broken", "invalid JSON");
  } finally { legacy.close(); }
  try { assert.throws(() => openChatStore(file, bus), /invalid record.*retained/); }
  finally { bus.close(); }
  const retained = new Database(file);
  try { assert.equal(retained.prepare("SELECT record_json FROM chats WHERE id='broken'").get().record_json, "invalid JSON"); }
  finally { retained.close(); }
});

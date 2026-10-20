import assert from "node:assert/strict";
import test from "node:test";
import { isStoppedLocalEva, loadContactDetails, providerProfileState } from "../web/src/peer-profile.ts";

const local = { id: "2", handle: "@local.magi", name: "Local", nickname: "", url: "http://localhost:1234", local: true, running: false };
const owner = { handle: local.handle, owner: true, public_key: null };

test("offline local EVA remains local and cannot expose stale provider forms", () => {
  assert.equal(isStoppedLocalEva(local), true);
  assert.equal(providerProfileState(local), "offline");
  assert.equal(providerProfileState(local, owner), "offline");
  assert.equal(providerProfileState({ ...local, running: true }), "unavailable");
  assert.equal(providerProfileState({ ...local, running: true }, owner), "ready");
  assert.equal(providerProfileState({ ...local, running: true }, { ...owner, handle: "@other.magi" }), "unavailable");
  assert.equal(providerProfileState({ ...local, running: true }, { ...owner, owner: false }), "unavailable");
  assert.equal(providerProfileState({ ...local, local: false }, owner), "remote");
  assert.equal(providerProfileState({ ...local, group: true }, owner), "group");
  assert.equal(isStoppedLocalEva({ ...local, id: "shell" }), false);
  assert.equal(isStoppedLocalEva({ ...local, online: true }), false);
});

test("stopped local EVA skips unavailable owner RPC without reporting Shell failure and retries after Start", async () => {
  const calls: string[] = [];
  const previous = Object.getOwnPropertyDescriptor(globalThis, "window");
  Object.defineProperty(globalThis, "window", { configurable: true, value: { magiDesktop: { invokeLocal: async (method: string) => {
    calls.push(method);
    return method === "peer.profile" ? owner : [];
  } } } });
  try {
    assert.deepEqual(await loadContactDetails(local), { id: local.id, failed: false });
    assert.deepEqual(calls, []);
    const started = await loadContactDetails({ ...local, running: true });
    assert.deepEqual(calls, ["peer.events", "peer.profile"]);
    assert.equal(started.failed, false);
    assert.equal(providerProfileState({ ...local, running: true }, started.details), "ready");
  } finally {
    if (previous) Object.defineProperty(globalThis, "window", previous);
    else Reflect.deleteProperty(globalThis, "window");
  }
});

test("individual profile failure preserves successfully loaded history without granting owner access", async () => {
  const history = [{ id: 1, content: "saved message", role: "assistant", created_at: "" }];
  const previous = Object.getOwnPropertyDescriptor(globalThis, "window");
  Object.defineProperty(globalThis, "window", { configurable: true, value: { magiDesktop: { invokeLocal: async (method: string) => {
    if (method === "peer.profile") throw new Error("EVA unavailable");
    return history;
  } } } });
  try {
    const result = await loadContactDetails({ ...local, running: true });
    assert.deepEqual(result.history, history);
    assert.equal(result.details, undefined);
    assert.equal(result.failed, true);
    assert.equal(result.id, local.id);
    assert.equal(providerProfileState({ ...local, running: true }, result.details), "unavailable");
  } finally {
    if (previous) Object.defineProperty(globalThis, "window", previous);
    else Reflect.deleteProperty(globalThis, "window");
  }
});

import assert from "node:assert/strict";
import { mkdtempSync, rmSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { Bus, SHELL_MAGI_HANDLE, MAGI_CONTACT_ID } from "@magi/bus";
import { createLocalApi } from "@magi/webapp/api.js";
import { WebappWorker } from "@magi/webapp/worker.js";

function fixture(t) {
  const home = mkdtempSync(join(tmpdir(), "shell-peer-test-"));
  const bus = new Bus(SHELL_MAGI_HANDLE, join(home, ".magi", "shell"));
  t.after(() => { bus.close(); rmSync(home, { recursive: true, force: true }); });
  let spawned = 0;
  const context = { bus, paths: { home, checkout: process.cwd() }, tools: { node: process.execPath, npm: import.meta.filename, git: "git", env: {} },
    command: async () => "", spawn: () => { spawned++; throw new Error("Unexpected spawn"); },
    nodeControl: { running: () => true, setRunning: async enabled => ({ running: enabled }) }, emit() {}, openExternal() {}, copy() {} };
  return { bus, context, spawned: () => spawned };
}

test("Shell startup presents only its built-in EVA and never starts ASP/default agent", async t => {
  const f = fixture(t); const api = createLocalApi(f.context);
  await api.start();
  assert.deepEqual(api["peer.contacts"]().map(c => c.id), ["shell"]);
  assert.equal(f.spawned(), 0);
  assert.equal("runtime.startAsp" in api, false);
  await api.dispose();
});

test("Owner chat input is recorded locally; lifecycle works without a model", async t => {
  const { bus, context } = fixture(t); const api = createLocalApi(context);
  await api["peer.send"]({ id: "shell", content: "Hello" });
  assert.equal((await api["peer.events"]({ id: "shell" }))[0].role, "user");
  await api["peer.send"]({ id: "shell", content: "", metadata: { local_eva_action: { contact_id: "shell", action: "stop" } } });
  assert.match((await api["peer.events"]({ id: "shell" })).at(-1).content, /false/);
  assert.equal(bus.chats.list()[0].channel, "shell-owner");
  await api.dispose();
});

test("Remote contact metadata cannot grant local process control", async t => {
  const { bus, context } = fixture(t); const api = createLocalApi(context);
  const contact = bus.contacts.forPeer({ principal: "a".repeat(64), endpoint: "https://eva.example", name: "@remote.magi" });
  bus.contacts.acceptPeer(contact.id);
  assert.equal(api["peer.contacts"]().length, 2);
  assert.equal(api["peer.contacts"]()[1].local, false);
  await assert.rejects(api["peer.send"]({ id: String(contact.id), content: "", metadata: { local_eva_action: { contact_id: contact.id, action: "start" } } }), /owner/);
  await assert.rejects(api.localManagement.find(t => t.name === "local_eva_lifecycle").run({ contact_id: contact.id, action: "start" }), /not a locally provisioned/);
  await api.dispose();
});

test("Management tool rejects a peer-triggered call even when it requests owner identity", async t => {
  const { bus, context } = fixture(t); const worker = new WebappWorker(bus, context);
  const room = bus.chats.forChannel("peer", "remote");
  const id = bus.messages.add(room.id, MAGI_CONTACT_ID, "create an EVA");
  const job = bus.board("RunToolJob").publish({ chat_id: room.id, message_id: id, call: { name: "create_local_eva", tool_call_id: "x", arguments: { name: "remote-spawn" } } }, "agent");
  await worker.poll(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(bus.board("RunToolJob").result(job)?.status, "failed");
  await worker.stop();
});

test("Shell composition has no ASP process or relay worker", () => {
  const shell = readFileSync(new URL("../../../packages/node/shell.ts", import.meta.url), "utf8");
  assert.doesNotMatch(shell, /AspProcessWorker|aspProcess/);
  const roles = readFileSync(new URL("../../../packages/node/roles.ts", import.meta.url), "utf8").split("export function shellWorkers")[1].split("HOSTED_NODE_WORKERS")[0];
  assert.doesNotMatch(roles, /new AspWorker/);
});

test("Local create waits for peer identity before publishing contact and rolls back failures", async t => {
  const { bus } = fixture(t);
  const { localEvaManagement } = await import("@magi/webapp/local-evas.js");
  let running: string[] = [];
  let stopped = 0;
  const runtime = {
    startLocal: async ({ handle }) => { running.push(handle); return { started: true }; },
    running: () => running,
    stopLocal: async handle => { stopped++; running = running.filter(h => h !== handle); return true; }, stop() {},
  };
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => {
    const identity = JSON.parse(readFileSync(join(bus.workspace, "..", "new-eva", "identity.json"), "utf8"));
    return new Response(JSON.stringify({ publicKey: identity.publicKey }), { status: 200 });
  };
  const tool = localEvaManagement(bus, runtime).find(t => t.name === "create_local_eva")!;
  const result = JSON.parse(await tool.run({ name: "new-eva" }));
  assert.equal(result.ready, true);
  assert.equal(bus.contacts.get(result.contact_id)?.peer_trust, "accepted");
  assert.equal(stopped, 0);
  await assert.rejects(tool.run({ name: "new-eva" }), /already exists/);
  globalThis.fetch = async () => new Response(JSON.stringify({ publicKey: bus.identity.publicKey }), { status: 200 });
  await assert.rejects(tool.run({ name: "failed-eva" }), /identity does not match/);
  assert.equal(stopped, 1);
  assert.equal(bus.contacts.list().some(c => c.name === "@failed-eva.magi"), false);
});

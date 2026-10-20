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

test("Local owner adapter exposes sealed provider chat without granting peer ownership", async t => {
  const { bus } = fixture(t);
  const { createOwnerApi, LocalOwnerWorker } = await import("@magi/node/owner.js");
  const api = createOwnerApi(bus);
  assert.equal(api["owner.profile"]().owner, true);
  assert.equal(api["owner.profile"]().public_key, bus.identity.publicKey);
  const receipt = api["owner.chat"]({ content: "", metadata: { card_result: { id: "provider", action: "open" } } });
  assert.equal(receipt.type, "InboundSecretJob");
  const room = bus.chats.list("local-owner")[0];
  const { messageDelivery } = await import("@magi/bus");
  messageDelivery.send(bus, room.id, "Configure provider", MAGI_CONTACT_ID, "manager", { card: { id: "provider" } });
  await new LocalOwnerWorker(bus).poll();
  assert.deepEqual(api["owner.events"]().at(-1)?.metadata, { card: { id: "provider" } });
  assert.equal("owner.stop" in api, false);
});

test("Group UI lists signed membership and uses deterministic event identities", async t => {
  const { bus, context } = fixture(t); const api = createLocalApi(context);
  bus.settings.set("peer.reachableURL", "http://127.0.0.1:49222");
  const { groupPrincipal } = await import("@magi/bus/books/groupBook.js");
  bus.settings.set("peer.principal", groupPrincipal(bus.identity.publicKey));
  const group = bus.groups.create({ title: "Test group", members: [] });
  const id = `conversation:${group.conversation_uuid}`;
  assert.equal(api["peer.conversations"]()[0].title, "Test group");
  await api["peer.send"]({ id, content: "hello group" });
  const events = await api["peer.events"]({ id });
  assert.equal(events[0].content, "hello group");
  assert.equal(typeof events[0].id, "string");
  await api.dispose();
});

test("Remote URL connection sends hello and leaves recipient acceptance independent", async t => {
  const local = fixture(t); const remote = fixture(t);
  const { PeerServer } = await import("@magi/channel-peer/server.js");
  const { messageDelivery } = await import("@magi/bus");
  const server = new PeerServer(remote.bus.identity, "@remote.magi", (principal, message) => messageDelivery.receivePeer(remote.bus, {
    principal, endpoint: message.reachableURL, conversationUUID: message.conversationUUID,
    messageId: message.messageId, content: message.content,
  }), { host: "127.0.0.1", port: 0 });
  await server.start(); t.after(() => server.stop());
  local.bus.settings.set("peer.reachableURL", "http://127.0.0.1:49221");
  const { discoverPeer, PeerClient } = await import("@magi/channel-peer/client.js");
  const client = new PeerClient(local.bus.identity);
  const api = createLocalApi({ ...local.context, peerTransport: { discover: discoverPeer, send: client.send.bind(client) } });
  const tool = api.localManagement.find(tool => tool.name === "connect_peer")!;
  await tool.run({ url: server.origin });
  assert.equal(api["peer.contacts"]().length, 2);
  assert.equal(api["peer.contacts"]()[1].local, false);
  const remoteContact = remote.bus.contacts.list().find(contact => contact.peer_id);
  assert.equal(remoteContact?.peer_trust, "pending");
  assert.equal(remoteContact?.role, "stranger");
  assert.equal(local.spawned(), 0);
  await api.dispose();
});

test("Built-in Shell reports running without an absent ASP worker", async t => {
  const { context } = fixture(t);
  const { Shell } = await import("@magi/node/shell.js");
  const shell = new Shell(context);
  try {
    await shell.start();
    assert.equal(shell.worker.methods["peer.contacts"]()[0].running, true);
    await shell.worker.methods["peer.send"]({ id: "shell", content: "", metadata: { local_eva_action: { contact_id: "shell", action: "stop" } } });
    assert.equal(shell.worker.methods["peer.contacts"]()[0].running, false);
    await shell.worker.methods["peer.send"]({ id: "shell", content: "", metadata: { local_eva_action: { contact_id: "shell", action: "start" } } });
    assert.equal(shell.worker.methods["peer.contacts"]()[0].running, true);
  } finally { await shell.stop(true); }
});


test("Local contact status uses the owned runtime handle rather than fingerprint name", async t => {
  const { bus, context } = fixture(t);
  const contact = bus.contacts.forPeer({ principal: "b".repeat(64), endpoint: "http://127.0.0.1:49223", name: "@owned-eva.magi" });
  bus.contacts.acceptPeer(contact.id);
  bus.contacts.update(contact.id, { name: `peer:${"b".repeat(64)}`, nickname: "@owned-eva.magi" });
  bus.settings.set(`local.eva.${contact.id}`, JSON.stringify({ handle: "@owned-eva.magi", port: 49223, url: "http://127.0.0.1:49223" }));
  const api = createLocalApi(context);
  api.localRuntime.running = () => ["@owned-eva.magi"];
  const row = api["peer.contacts"]().find(row => row.id === String(contact.id));
  assert.equal(row?.handle, "@owned-eva.magi");
  assert.equal(row?.running, true);
  assert.equal(row?.local, true);
  await api.dispose();
});

import assert from "node:assert/strict";
import { afterEach, test, sleep } from "./test.js";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createOwnerApi } from "@magi/node/owner.js";
import { Magi } from "../eva.js";
import { chat, messageDelivery, MAGI_CONTACT_ID, SYSTEM_CONTACT_ID, type LLMRequest } from "@magi/bus";

const paths: string[] = [];
afterEach(async () => { for (const path of paths.splice(0)) await rm(path, { recursive: true, force: true }); });
async function workspace() { const path = await mkdtemp(join(tmpdir(), "magi-subconscious-")); paths.push(path); return path; }
async function until(check: () => boolean) { for (let i = 0; i < 300 && !check(); i++) await sleep(10); assert.ok(check(), "condition did not settle"); }

// Presets are tested in tasks.test; these tests explicitly wake the same conversation.
test("subconscious continues one private history and cannot use outward tools", async () => {
  const delivered: string[] = [];
  const seen: LLMRequest[] = [];
  const magi = new Magi("@private.magi", { workspace: await workspace(), deliver: text => delivered.push(text), client: {
    async complete(request) {
      seen.push(structuredClone(request));
      if (seen.length === 1) return { role: "assistant", content: "private narration", tool_calls: [{ tool_call_id: "forbidden", name: "send_message", arguments: { chat_id: 1, text: "leak" } }] };
      return { role: "assistant", content: "private working note" };
    },
  } });
  try {
    await magi.start();
    const inner = magi.bus.chats.forChannel("subconscious", "self");
    assert.throws(() => chat(magi.bus, inner.id, SYSTEM_CONTACT_ID, { content: "external injection" }), /internal/);
    assert.throws(() => messageDelivery.send(magi.bus, inner.id, "external injection", SYSTEM_CONTACT_ID, "peer"), /internal/);
    assert.throws(() => messageDelivery.setHomeChat(magi.bus, inner.id), /internal/);
    const first = messageDelivery.send(magi.bus, inner.id, "continue", undefined, "task");
    await until(() => Boolean(magi.bus.board("MessageDeliveryJob").result(first)));
    assert.deepEqual(delivered, []);
    assert.equal(seen[0].tools.some(tool => tool.name === "send_message"), false);
    assert.equal(seen[0].tools.some(tool => tool.name === "handoff_to_foreground"), true);
    assert.match(seen[1].messages.at(-1)!.content, /not available/);
    const second = messageDelivery.send(magi.bus, inner.id, "continue again", undefined, "task");
    await until(() => Boolean(magi.bus.board("MessageDeliveryJob").result(second)));
    assert.ok(seen.at(-1)!.messages.some(message => message.content === "private working note"));
    assert.equal(magi.bus.chats.list("subconscious").length, 1);
    assert.deepEqual(delivered, []);
  } finally { await magi.stop(); }
});

test("a background model call does not occupy the foreground slot", async () => {
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  let backgroundStarted = false;
  const delivered: string[] = [];
  const magi = new Magi("@parallel.magi", { workspace: await workspace(), deliver: text => delivered.push(text), client: {
    async complete(request) {
      if (request.messages[0].content.includes("continuous private conversation")) {
        backgroundStarted = true;
        await held;
        return { role: "assistant", content: "private complete" };
      }
      return { role: "assistant", content: "foreground reply" };
    },
  } });
  try {
    await magi.start();
    const inner = magi.bus.chats.forChannel("subconscious", "self");
    const job = messageDelivery.send(magi.bus, inner.id, "continue", undefined, "task");
    await until(() => backgroundStarted);
    const user = magi.chat("hello");
    await until(() => delivered.includes("foreground reply"));
    assert.equal(magi.bus.board("MessageDeliveryJob").result(job), null);
    release();
    await user;
    await until(() => Boolean(magi.bus.board("MessageDeliveryJob").result(job)));
    assert.deepEqual(delivered, ["foreground reply"]);
  } finally { release(); await magi.stop(); }
});

test("an explicit internal handoff wakes the foreground, not a second private conversation", async () => {
  const delivered: string[] = [];
  let backgroundCalls = 0;
  const magi = new Magi("@handoff.magi", { workspace: await workspace(), deliver: text => delivered.push(text), client: {
    async complete(request) {
      if (request.messages[0].content.includes("continuous private conversation")) {
        if (++backgroundCalls === 1) return { role: "assistant", content: "", tool_calls: [{ tool_call_id: "handoff", name: "handoff_to_foreground", arguments: { finding: "An open commitment needs review." } }] };
        return { role: "assistant", content: "handoff recorded" };
      }
      assert.ok(!request.tools.some(tool => tool.name === "handoff_to_foreground"));
      assert.match(request.messages.at(-1)!.content, /not a user instruction or authorization/);
      return { role: "assistant", content: "useful foreground update" };
    },
  } });
  try {
    await magi.start();
    const owner = createOwnerApi(magi.bus);
    const home = magi.bus.chats.forChannel("cli", "terminal");
    // The first handoff is retained for reasoning, but never presented as user text.
    const ownerChat = magi.bus.chats.list("local-owner")[0];
    magi.bus.deliveries.persist({ chat_id: ownerChat.id, contact_id: SYSTEM_CONTACT_ID, content: "internal finding", publisher: "subconscious", enqueue: false });
    assert.deepEqual(owner["owner.events"](), []);
    messageDelivery.setHomeChat(magi.bus, home.id);
    const inner = magi.bus.chats.forChannel("subconscious", "self");
    messageDelivery.send(magi.bus, inner.id, "continue", undefined, "task");
    await until(() => delivered.length === 1);
    assert.deepEqual(delivered, ["useful foreground update"]);
  } finally { await magi.stop(); }
});

test("private failures and restart notices remain private", async () => {
  const path = await workspace();
  const delivered: string[] = [];
  const magi = new Magi("@private-errors.magi", { workspace: path, deliver: text => delivered.push(text), client: {
    async complete() { throw new Error("private failure"); },
  } });
  let innerId = 0;
  try {
    await magi.start();
    innerId = magi.bus.chats.forChannel("subconscious", "self").id;
    const job = messageDelivery.send(magi.bus, innerId, "continue", undefined, "task");
    await until(() => Boolean(magi.bus.board("MessageDeliveryJob").result(job)));
    assert.deepEqual(delivered, []);
    for (const message of magi.bus.messages.list(innerId).filter(message => message.contact_id === MAGI_CONTACT_ID)) {
      assert.equal(magi.bus.deliveries.get(message.id), undefined, "private output must not enqueue outward delivery");
    }
    assert.ok(magi.bus.messages.list(innerId).some(message => message.contact_id === SYSTEM_CONTACT_ID && message.content === "private failure"));
  } finally { await magi.stop(); }
  const reopened = new Magi("@private-errors.magi", { workspace: path });
  const queued = messageDelivery.send(reopened.bus, innerId, "interrupted", undefined, "task");
  reopened.bus.board("MessageDeliveryJob").claimId("agent", queued);
  reopened.bus.agentTurns.ensure(queued, { messages: [], tools: [] });
  await reopened.stop();
  const recovered = new Magi("@private-errors.magi", { workspace: path });
  try {
    assert.ok(recovered.bus.messages.list(innerId).some(message => message.content.includes("this turn was interrupted")));
    assert.equal(recovered.bus.chats.list("subconscious").length, 1);
    assert.equal(recovered.bus.board("MessageDeliveryJob").result(queued)?.status, "failed");
  } finally { await recovered.stop(); }
});

test("both role compositions own exactly one task worker and one private conversation", async () => {
  const { Bus, BaseWorker } = await import("@magi/bus");
  const { agentWorkers, shellWorkers, HOSTED_NODE_WORKERS } = await import("@magi/node/roles.js");
  const bus = new Bus("@roles.magi", await workspace());
  class EmptyWorker extends BaseWorker { readonly worker_name = "empty"; async poll() { return false; } }
  try {
    const agent = agentWorkers(bus);
    assert.equal(agent.filter(worker => worker.worker_name === "task").length, 1);
    const shell = shellWorkers(bus, new EmptyWorker(bus), new EmptyWorker(bus));
    assert.equal(shell.filter(worker => worker.worker_name === "task").length, 1);
    assert.ok(HOSTED_NODE_WORKERS.includes("task"));
    const task = shell.find(worker => worker.worker_name === "task")! as import("@magi/channel-tasks/worker.js").TaskWorker;
    await task.start();
    await task.start();
    assert.equal(bus.chats.list("subconscious").length, 1);
    await task.stop();
  } finally { bus.close(); }
});

test("private context rolls through compaction without inventing a user participant", async () => {
  const delivered: string[] = [];
  let compacted = false;
  let continued = false;
  const magi = new Magi("@rolling.magi", { workspace: await workspace(), deliver: text => delivered.push(text), client: {
    async complete(request) {
      if (request.messages[0].content.includes("private conversation with itself")) {
        compacted = true;
        return { role: "assistant", content: "Observed through message 42; onboarding preference question remains open." };
      }
      continued = request.messages[0].content.includes("Observed through message 42");
      return { role: "assistant", content: "private continuation" };
    },
  } });
  try {
    await magi.start();
    magi.bus.settings.set("provider.context_window", "1000");
    const inner = magi.bus.chats.forChannel("subconscious", "self");
    for (let i = 0; i < 30; i++) messageDelivery.record(magi.bus, inner.id, `Working note ${i}: ${"context ".repeat(30)}`, MAGI_CONTACT_ID);
    const job = messageDelivery.send(magi.bus, inner.id, "continue", undefined, "task");
    await until(() => Boolean(magi.bus.board("MessageDeliveryJob").result(job)));
    assert.ok(compacted && continued);
    assert.match(magi.bus.chats.get(inner.id)!.summary, /message 42/);
    assert.deepEqual(delivered, []);
  } finally { await magi.stop(); }
});

test("ordinary message searches omit private history but preserve shared foreground and raw audit", async () => {
  const { messageTools } = await import("@magi/built-in-tools/messages.js");
  const { subconsciousTools } = await import("@magi/agent/subconscious.js");
  const magi = new Magi("@history-boundary.magi", { workspace: await workspace() });
  try {
    const inner = magi.bus.chats.forChannel("subconscious", "self");
    const foreground = magi.bus.chats.forChannel("cli", "terminal");
    magi.bus.messages.add(foreground.id, MAGI_CONTACT_ID, "search-token foreground");
    for (let i = 0; i < 25; i++) magi.bus.messages.add(inner.id, MAGI_CONTACT_ID, "search-token private");
    const tools = messageTools(magi.bus);
    const privateResult = JSON.parse(await tools.find(tool => tool.name === "search_chat_messages")!.run({ chat_id: inner.id, query: "search-token" }));
    assert.deepEqual(privateResult.messages, []);
    const contactResult = JSON.parse(await tools.find(tool => tool.name === "search_contact_messages")!.run({ contact_id: MAGI_CONTACT_ID, query: "search-token", limit: 1 }));
    assert.equal(contactResult.messages[0].content, "search-token foreground", "filter before limit, not after");
    assert.equal(magi.bus.messages.searchChat(inner.id, "search-token").length, 20, "raw audit remains available");
    magi.bus.deliveries.persist({ chat_id: foreground.id, contact_id: SYSTEM_CONTACT_ID, content: "own internal finding", publisher: "subconscious", enqueue: false });
    const read = JSON.parse(await subconsciousTools(magi.bus).find(tool => tool.name === "read_conversation")!.run({ chat_id: foreground.id }));
    assert.equal(read.messages.some((message: { content: string }) => message.content === "own internal finding"), false);
    assert.equal(read.messages[0].content, "search-token foreground");
  } finally { await magi.stop(); }
});

import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, rmSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Bus, SHELL_MAGI_HANDLE, type BaseWorker, type LLMRequest } from "@magi/bus";
import { FeedWorker } from "@magi/feed";
import { feedApi } from "@magi/webapp/feed.js";
import { McpWorker } from "@magi/mcp/worker.js";
import { ProvidersWorker } from "@magi/providers/worker.js";
import { SubconsciousWorker } from "@magi/subconscious/worker.js";
import { TaskWorker } from "@magi/channel-tasks/worker.js";
import { agentWorkers } from "@magi/launcher/composition/roles.js";
const sleep = (ms = 5) => new Promise(resolve => setTimeout(resolve, ms));
function fixture() {
  const root = mkdtempSync(join(tmpdir(), "magi-feed-"));
  const bus = new Bus(SHELL_MAGI_HANDLE, root);
  const feed = new FeedWorker(bus);
  const inner = bus.chats.forChannel("subconscious", "self");
  const owner = bus.chats.forChannel("shell-owner", "owner");
  const api = feedApi(bus);
  const board = bus.board("RunToolJob");
  let queries = 0, connectionGeneration = 0;
  const destinations: string[] = [];
  bus.mcpServers.save({ name: "public", connection_type: "stdio", command: "unused-mock", args: [], env: {}, headers: {}, enabled: true, url: null, connect_timeout: null, execute_timeout: null });
  const mcp = new McpWorker(bus, async config => { const instance = `connection-${++connectionGeneration}`; return { close: async () => {}, tools: [
    { name: "public__search", description: "Public web search", input_schema: { type: "object", properties: { query: { type: "string" } } }, researchSource: { origin: "mcp", readOnlyHint: true, fingerprint: config.command === "unused-mock" ? "public-v1" : `public-${config.command}`, instance }, run: async () => { queries++; destinations.push(config.command ?? ""); return 'A new public discovery. https://example.org/discovery'; } },
    { name: "public__write", description: "Not a research source", input_schema: { type: "object" }, run: async () => { throw new Error("must never execute"); } },
  ] }; });
  const workers: BaseWorker[] = [feed, mcp];
  async function pump(until: () => boolean) {
    const deadline = Date.now() + 5000;
    while (!until()) { assert.ok(Date.now() < deadline, "workers did not settle"); for (const worker of workers) await worker.poll(); await sleep(); }
  }
  async function run(name: string, args: Record<string, unknown>, chat_id = inner.id, publisher = "subconscious") {
    const id = board.publish({ chat_id, call: { name, arguments: args, tool_call_id: `test-${name}` } }, publisher);
    await pump(() => Boolean(board.result(id)));
    return board.result(id)!;
  }
  async function configure(extra: Record<string, unknown> = {}) {
    return run("configure_feed", { enabled: true, prompt: "Discover public scientific advances", sources: ["public__search"], source_fingerprints: { "public__search": "public-v1" }, ...extra }, owner.id, "webapp-feed");
  }
  return { bus, root, feed, inner, owner, api, board, mcp, workers, pump, run, configure, destinations, get queries() { return queries; }, async start() { await mcp.start(); await feed.start(); }, async close() { await feed.stop(); await mcp.stop(); bus.close(); rmSync(root, { recursive: true, force: true }); } };
}

test("Feed is Shell-only, absent from headless composition and withdrawn on stop", async () => {
  const f = fixture();
  try {
    assert.ok(!agentWorkers(f.bus).some(worker => worker.worker_name === "feed"));
    assert.ok(!f.bus.tools.catalog({ chat_id: f.inner.id }).some(tool => tool.name === "research_feed"));
    await f.feed.start();
    assert.ok(f.bus.tools.catalog({ chat_id: f.inner.id }).some(tool => tool.name === "research_feed"));
    assert.ok(!f.bus.tools.catalog({ chat_id: f.owner.id }).some(tool => tool.name === "research_feed"));
    assert.deepEqual(f.bus.tasks.list(), []);
    await f.feed.stop();
    assert.equal(f.bus.tools.get("research_feed"), null);
    assert.deepEqual(f.bus.prompts.sections(f.inner.id).filter(section => section.title === "Interest feed"), []);
    const other = new Bus("@headless.magi", join(f.root, "other"));
    try { assert.throws(() => new FeedWorker(other), /only on the Shell/); } finally { other.close(); }
  } finally { await f.close(); }
});

test("owner selection is mandatory; model/peer cannot configure, research, or give owner feedback", async () => {
  const f = fixture();
  try {
    await f.start();
    assert.equal(f.api["feed.state"]().status, "disabled");
    assert.equal((await f.run("research_feed", { tool_name: "public__search", arguments: {} })).status, "failed");
    assert.equal((await f.run("configure_feed", { enabled: true, prompt: "x", sources: ["public__search"] }, f.owner.id, "agent")).status, "failed");
    assert.equal((await f.configure({ sources: ["public__write"] })).status, "failed");
    assert.equal((await f.configure()).status, "completed");
    const peer = f.bus.chats.forChannel("peer", "someone");
    assert.equal((await f.run("research_feed", { tool_name: "public__search", arguments: {} }, peer.id)).status, "failed");
    assert.equal((await f.run("list_feed_items", {}, peer.id)).status, "failed");
    assert.equal((await f.run("research_feed", { tool_name: "public__write", arguments: {} })).status, "failed");
    assert.equal(f.queries, 0);
    assert.ok(!f.bus.tools.catalog({ chat_id: f.inner.id }).some(tool => tool.name === "configure_feed"));
    assert.deepEqual(f.bus.prompts.sections(peer.id), []);
    assert.equal((await f.configure({ sources: [] })).status, "completed");
    assert.equal(f.api["feed.state"]().status, "sourceNeeded");
  } finally { await f.close(); }
});

test("real BUS research results back citations, dedupe, feedback, and persistent prompt", async () => {
  const f = fixture();
  try {
    await f.start(); await f.configure();
    const result = await f.run("research_feed", { tool_name: "public__search", arguments: { query: "public science" } });
    assert.equal(result.status, "completed");
    const evidence = JSON.parse(result.output!.content).evidence;
    assert.equal(evidence.kind, "search-snippet");
    assert.deepEqual(evidence.urls, ["https://example.org/discovery"]);
    const input = { title: "Discovery", body: "A reported result, based on its search excerpt.", why_relevant: "Matches your science interests", sources: [{ evidence_id: evidence.id, url: evidence.urls[0] }] };
    assert.equal((await f.run("save_feed_item", { ...input, sources: [{ evidence_id: evidence.id, url: "https://example.org/invented" }] })).status, "failed");
    assert.equal((await f.run("save_feed_item", input)).status, "completed");
    assert.equal((await f.run("save_feed_item", { ...input, title: "Different model title" })).status, "completed");
    assert.equal(f.bus.feed.list().length, 1);
    const item = f.bus.feed.list()[0];
    assert.equal((await f.run("set_feed_feedback", { id: item.id, feedback: "dismiss", expected_version: 1 })).status, "failed");
    assert.equal((await f.run("set_feed_feedback", { id: item.id, feedback: "dismiss", expected_version: 1 }, f.owner.id, "webapp-feed")).status, "completed");
    assert.equal((await f.run("set_feed_feedback", { id: item.id, feedback: "useful", expected_version: 1 }, f.owner.id, "webapp-feed")).status, "failed");
    assert.equal((await f.run("save_feed_item", input)).status, "completed");
    assert.deepEqual(f.bus.feed.list(), []);
    await f.feed.stop(); await f.mcp.stop(); f.bus.close();
    const reopened = new Bus(SHELL_MAGI_HANDLE, f.root);
    try {
      assert.equal(reopened.feed.list(true)[0].feedback, "dismiss");
      assert.equal(reopened.prompts.get("feed/AGENT"), "Discover public scientific advances");
      assert.equal(reopened.feed.evidence(evidence.id)?.kind, "search-snippet");
    } finally { reopened.close(); }
    // This fixture has already closed its original BUS.
    rmSync(f.root, { recursive: true, force: true });
  } catch (error) { await f.close(); throw error; }
});

test("research daily budget is an adjustable default, not an implicit user limit", async () => {
  const f = fixture();
  try {
    await f.start(); await f.configure({ daily_budget: 1 });
    assert.equal((await f.run("research_feed", { tool_name: "public__search", arguments: {} })).status, "completed");
    assert.equal((await f.run("research_feed", { tool_name: "public__search", arguments: {} })).status, "failed");
    assert.equal(f.queries, 1);
    await f.configure({ daily_budget: 2 });
    assert.equal((await f.run("research_feed", { tool_name: "public__search", arguments: {} })).status, "completed");
    assert.equal(f.queries, 2);
    assert.equal(f.api["feed.state"]().daily_budget, 2);
  } finally { await f.close(); }
});

test("owner withdrawal cancels queued research; stopped Feed cannot accept late results", async () => {
  const f = fixture();
  try {
    await f.start(); await f.configure();
    const pending = f.board.publish({ chat_id: f.inner.id, call: { name: "research_feed", arguments: { tool_name: "public__search", arguments: {} }, tool_call_id: "pending" } }, "subconscious");
    await f.feed.poll(); // Publishes the source request, deliberately not executed by MCP yet.
    const revoke = f.board.publish({ chat_id: f.owner.id, call: { name: "configure_feed", arguments: { enabled: false, prompt: "x", sources: [], source_fingerprints: {} }, tool_call_id: "revoke" } }, "webapp-feed");
    await f.feed.poll();
    await f.pump(() => Boolean(f.board.result(revoke)) && Boolean(f.board.result(pending)));
    assert.equal(f.board.result(pending)?.status, "failed");
    assert.equal(f.queries, 0);
    assert.equal(f.bus.feed.list().length, 0);
  } finally { await f.close(); }
});

test("existing Task -> subconscious -> provider -> Feed -> MCP path produces sourced cards without raw MCP access", async () => {
  const f = fixture();
  let calls = 0;
  const requests: LLMRequest[] = [];
  const subconscious = new SubconsciousWorker(f.bus);
  const provider = new ProvidersWorker(f.bus, { async complete(request) {
    requests.push(structuredClone(request)); calls++;
    if (calls === 1) return { role: "assistant", content: "", tool_calls: [{ tool_call_id: "research", name: "research_feed", arguments: { tool_name: "public__search", arguments: { query: "public science" } } }] };
    if (calls === 2) {
      const evidence = JSON.parse(request.messages.at(-1)!.content).evidence;
      return { role: "assistant", content: "", tool_calls: [{ tool_call_id: "publish", name: "save_feed_item", arguments: { title: "Science discovery", body: "Reported in a search excerpt.", why_relevant: "Relevant to your interests.", sources: [{ evidence_id: evidence.id, url: evidence.urls[0] }] } }] };
    }
    return { role: "assistant", content: "NO_REPLY" };
  } });
  const tasks = new TaskWorker(f.bus, { isReady: () => true });
  try {
    await f.start(); await f.configure(); await subconscious.start(); await provider.start(); await tasks.start();
    f.workers.push(subconscious, provider, tasks);
    const preset = f.bus.tasks.list().find(task => task.name === "Subconscious review")!;
    f.bus.tasks.update(preset.id, { cron: "* * * * *" });
    f.bus.tasks.markFired(preset.id, "2000-01-01T00:00");
    await f.pump(() => f.bus.feed.list().length === 1 && calls >= 3);
    assert.equal(f.bus.tasks.list().length, 1, "Feed did not create its own timer");
    assert.equal(f.queries, 1);
    assert.ok(requests[0].messages[0].content.includes("Discover public scientific advances"));
    assert.ok(requests[0].tools.some(tool => tool.name === "research_feed"));
    assert.ok(!requests[0].tools.some(tool => tool.name === "public__search"));
    assert.ok(!requests[0].tools.some(tool => tool.name === "public__write"));
    assert.equal(f.api["feed.state"]().items[0].sources[0].kind, "search-snippet");
    assert.equal(f.bus.actionItems.list().length, 3, "Feed did not create a goal or idea beyond existing onboarding notes");
  } finally { await tasks.stop(); await subconscious.stop(); await provider.stop(); await f.close(); }
});

test("Feed UI has its own route and labels evidence limits without unsafe HTML rendering", () => {
  const source = readFileSync(new URL("../../../../apps/shell/web/src/FeedPage.tsx", import.meta.url), "utf8");
  assert.ok(source.includes('"feed.configure"'));
  assert.ok(source.includes('"feed.snippet"'));
  assert.ok(!source.includes("dangerouslySetInnerHTML"));
});

test("local Feed owner API travels through BUS and historical cards remain readable while paused", async () => {
  const f = fixture();
  try {
    await f.start();
    let settled = false;
    const change = f.api["feed.configure"]({ enabled: true, prompt: "Owner-edited science interests", sources: ["public__search"], source_fingerprints: { "public__search": "public-v1" }, daily_budget: 4 }).finally(() => { settled = true; });
    await f.pump(() => settled); await change;
    assert.equal(f.api["feed.state"]().prompt, "Owner-edited science interests");
    assert.equal(f.api["feed.state"]().daily_budget, 4);
    await f.feed.stop();
    assert.equal(f.api["feed.state"]().status, "paused");
    await assert.rejects(f.api["feed.configure"]({ enabled: false, prompt: "x", sources: [] }), /not running/);
  } finally { await f.close(); }
});

test("MCP rechecks a queued source's read-only declaration before execution", async () => {
  const f = fixture();
  try {
    await f.start(); await f.configure();
    const id = f.board.publish({ chat_id: f.inner.id, call: { name: "research_feed", arguments: { tool_name: "public__search", arguments: {} }, tool_call_id: "changed-capability" } }, "subconscious");
    await f.feed.poll();
    f.bus.tools.get("public__search")!.researchSource!.readOnlyHint = false;
    await f.pump(() => Boolean(f.board.result(id)));
    assert.equal(f.board.result(id)?.status, "failed");
    assert.match(f.api["feed.state"]().last_error ?? "", /no longer declares read-only/);
    assert.equal(f.queries, 0);
    assert.deepEqual(f.bus.feed.list(), []);
  } finally { await f.close(); }
});

test("Feed stop interrupts its wait and never publishes a source's late completion", async () => {
  const f = fixture();
  let release!: (value: string) => void;
  try {
    await f.start(); await f.configure();
    f.bus.tools.get("public__search")!.run = () => new Promise<string>(resolve => { release = resolve; });
    const id = f.board.publish({ chat_id: f.inner.id, call: { name: "research_feed", arguments: { tool_name: "public__search", arguments: {} }, tool_call_id: "stopping" } }, "subconscious");
    await f.feed.poll(); await f.mcp.poll();
    await f.feed.stop();
    assert.equal(f.board.result(id)?.status, "failed");
    release("late https://example.org/late"); await sleep(30);
    assert.deepEqual(f.bus.feed.list(), []);
    assert.equal(f.bus.feed.evidence(1), null);
  } finally { release?.(""); await f.close(); }
});

test("claimed research cannot resurrect after owner disable and re-enable", async () => {
  const f = fixture();
  let release!: (value: string) => void;
  try {
    await f.start(); await f.configure();
    f.bus.tools.get("public__search")!.run = () => new Promise<string>(resolve => { release = resolve; });
    const id = f.board.publish({ chat_id: f.inner.id, call: { name: "research_feed", arguments: { tool_name: "public__search", arguments: {} }, tool_call_id: "revoke-restore" } }, "subconscious");
    await f.feed.poll(); await f.mcp.poll();
    assert.equal((await f.configure({ enabled: false, sources: [] })).status, "completed");
    assert.equal((await f.configure()).status, "completed");
    release("old withdrawn https://example.org/old");
    await f.pump(() => Boolean(f.board.result(id)));
    assert.equal(f.board.result(id)?.status, "failed");
    assert.equal(f.bus.feed.evidence(1), null);
  } finally { release?.(""); await f.close(); }
});

test("queued research cannot execute against a same-name changed destination; selection must be renewed", async () => {
  const f = fixture();
  try {
    await f.start(); await f.configure();
    const id = f.board.publish({ chat_id: f.inner.id, call: { name: "research_feed", arguments: { tool_name: "public__search", arguments: {} }, tool_call_id: "replace-source" } }, "subconscious");
    await f.feed.poll();
    f.bus.board("ChangeMcpServerNotify").publish({ action: "update", name: "public", server: { ...f.bus.mcpServers.get("public")!, command: "new-unselected-mock" } }, "test-owner");
    await f.mcp.poll();
    await f.pump(() => Boolean(f.board.result(id)));
    assert.equal(f.board.result(id)?.status, "failed");
    assert.deepEqual(f.destinations, []);
    assert.equal(f.bus.feed.evidence(1), null);
    const state = f.api["feed.state"]();
    assert.equal(state.status, "sourceNeeded");
    assert.deepEqual(state.selected, []);
    assert.deepEqual(state.unavailable_selected, ["public__search"]);
    assert.equal((await f.configure()).status, "failed", "stale owner UI cannot silently reauthorize a new source");
    assert.equal((await f.run("research_feed", { tool_name: "public__search", arguments: {} })).status, "failed");
    assert.equal((await f.configure({ source_fingerprints: { "public__search": "public-new-unselected-mock" } })).status, "completed");
    assert.equal((await f.run("research_feed", { tool_name: "public__search", arguments: {} })).status, "completed");
    assert.deepEqual(f.destinations, ["new-unselected-mock"]);
  } finally { await f.close(); }
});

test("an unchanged reconnect retains future selection but never revives an old connection's claimed result", async () => {
  const f = fixture();
  let release!: (value: string) => void;
  try {
    await f.start(); await f.configure();
    f.bus.tools.get("public__search")!.run = () => new Promise<string>(resolve => { release = resolve; });
    const id = f.board.publish({ chat_id: f.inner.id, call: { name: "research_feed", arguments: { tool_name: "public__search", arguments: {} }, tool_call_id: "reconnect" } }, "subconscious");
    await f.feed.poll(); await f.mcp.poll();
    f.bus.board("ChangeMcpServerNotify").publish({ action: "update", name: "public", server: f.bus.mcpServers.get("public")! }, "test-owner");
    await f.mcp.poll();
    assert.deepEqual(f.api["feed.state"]().selected, ["public__search"]);
    release("old connection https://example.org/old");
    await f.pump(() => Boolean(f.board.result(id)));
    assert.equal(f.board.result(id)?.status, "failed");
    assert.equal(f.bus.feed.evidence(1), null);
    assert.equal((await f.run("research_feed", { tool_name: "public__search", arguments: {} })).status, "completed");
  } finally { release?.(""); await f.close(); }
});

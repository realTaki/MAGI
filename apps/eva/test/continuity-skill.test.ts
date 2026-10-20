import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test, sleep } from "./test.js";
import { BaseWorker, Bus, MAGI_CONTACT_ID, messageDelivery, type LLMRequest } from "@magi/bus";
import { agentWorkers, shellWorkers } from "@magi/launcher/composition/roles.js";
import { Magi } from "./harness.js";
import type { SkillWorker } from "@magi/skills/worker.js";

const NAME = "subconscious_continuity";

// Both compositions use the existing SkillWorker. Seeding must remain additive:
// upgrading MAGI must never replace an EVA's own edited skill instructions.
test("all EVA roles install the continuity skill and preserve their edited copy", async () => {
  const path = await mkdtemp(join(tmpdir(), "magi-continuity-seed-"));
  class EmptyWorker extends BaseWorker { readonly worker_name = "empty"; async poll() { return false; } }
  try {
    for (const role of ["agent", "shell"] as const) {
      const bus = new Bus(`@continuity-${role}.magi`, join(path, role));
      const workers = role === "agent" ? agentWorkers(bus) : shellWorkers(bus, new EmptyWorker(bus), new EmptyWorker(bus));
      const skill = workers.find(worker => worker.worker_name === "skills")! as SkillWorker;
      try {
        await skill.start();
        const file = join(bus.workspace, "skills", NAME, "SKILL.md");
        const shipped = await readFile(file, "utf8");
        assert.equal(bus.skills.read(NAME), shipped);
        assert.match(bus.prompts.sections(1).find(section => section.title === "Available skills")!.body, /subconscious_continuity/);
        assert.match(shipped, /Finishing a related todo is not proof that the goal is achieved/);
        const edited = `${shipped}\nLocal EVA annotation: preserve this edit.\n`;
        await writeFile(file, edited);
        await skill.stop();
        await skill.start();
        assert.equal(await readFile(file, "utf8"), edited);
        assert.equal(bus.skills.read(NAME), edited);
      } finally { await skill.stop(); bus.close(); }
    }
  } finally { await rm(path, { recursive: true, force: true }); }
});

// A scripted model checks the actual private conversation and BUS tool route,
// not just a markdown match or a direct call to a tool implementation.
test("private conversation loads continuity skill and reconciles shared records without an external send", async () => {
  const path = await mkdtemp(join(tmpdir(), "magi-continuity-use-"));
  const delivered: string[] = [];
  let stage = 0;
  let sourceId = 0;
  let goalId = 0;
  let todoId = 0;
  let ideaId = 0;
  let taskId = 0;
  let cursor = 0;
  const call = (name: string, args: Record<string, unknown>) => ({ tool_call_id: `${stage}-${name}`, name, arguments: args });
  const result = (request: LLMRequest, name: string) => {
    const found = request.messages.findLast(message => message.role === "tool" && message.tool_call_id?.endsWith(`-${name}`));
    assert.ok(found, `missing ${name} result`);
    return JSON.parse(found.content);
  };
  const magi = new Magi("@continuity.magi", { workspace: path, deliver: text => delivered.push(text), client: {
    async complete(request) {
      stage++;
      assert.match(request.messages[0].content, /continuous private conversation/);
      assert.equal(request.tools.some(tool => tool.name === "send_message"), false);
      if (stage === 1) {
        assert.match(request.messages[0].content, /subconscious_continuity/);
        assert.ok(request.tools.some(tool => tool.name === "load_skill"));
        return { role: "assistant", content: "", tool_calls: [call("load_skill", { name: NAME })] };
      }
      if (stage === 2) {
        const loaded = request.messages.at(-1)!.content;
        assert.match(loaded, /# Subconscious continuity/);
        assert.match(loaded, /expected_version/);
        assert.match(loaded, /unchanged finding does not need another handoff/);
        return { role: "assistant", content: "", tool_calls: [call("observe_conversations", { after_message_id: 0 })] };
      }
      if (stage === 3) {
        const observed = result(request, "observe_conversations");
        assert.ok(observed.messages.some((message: { id: number }) => message.id === sourceId));
        cursor = observed.next_after_message_id;
        return { role: "assistant", content: "", tool_calls: [
          call("list_action_items", { include_archived: true }), call("list_memory", { include_archived: true }), call("list_tasks", {}),
        ] };
      }
      if (stage === 4) {
        const items = result(request, "list_action_items").action_items as { id: number; version: number }[];
        const version = (id: number) => items.find(item => item.id === id)!.version;
        assert.deepEqual(result(request, "list_memory").memories, []);
        assert.ok(result(request, "list_tasks").some((task: { id: number }) => task.id === taskId));
        return { role: "assistant", content: "", tool_calls: [
          call("archive_action_item", { action_item_id: todoId, expected_version: version(todoId) }),
          // Distinct call IDs are needed for two calls to the same tool.
          { ...call("archive_action_item", { action_item_id: ideaId, expected_version: version(ideaId) }), tool_call_id: "archive-idea" },
          call("save_action_item", { action_item_id: goalId, expected_version: version(goalId), content: `Goal: ship a reliable export. Step one finished; reliability tests remain. Source message ${sourceId}.` }),
          call("save_memory", { topic: "Review preference", detail: `User prefers concise reviews; explicitly stated in message ${sourceId}.`, kind: "long_term" }),
          call("update_task", { task_id: taskId, enabled: false }),
        ] };
      }
      assert.equal(stage, 5);
      assert.equal(result(request, "archive_action_item").action_item.archived, true);
      assert.equal(result(request, "save_action_item").action_item.category, "goal");
      assert.equal(result(request, "save_memory").memory.version, 1);
      assert.equal(result(request, "update_task").enabled, false);
      return { role: "assistant", content: `Processed through message ${cursor}. Todo completed and idea withdrawn; goal remains open pending reliability tests. No timely new handoff needed.` };
    },
  } });
  try {
    const chat = magi.bus.chats.forChannel("test-owner", "terminal");
    const source = magi.bus.messages.add(chat.id, magi.bus.userContactId, "Step one is finished, but reliable export is still the goal and reliability tests remain. Drop the optional dashboard idea and disable its scheduled review. I prefer concise reviews.");
    sourceId = source;
    goalId = magi.bus.actionItems.save({ category: "goal", content: "Ship a reliable export; step one and reliability tests remain." }).id;
    todoId = magi.bus.actionItems.save({ category: "todo", content: "Finish export step one." }).id;
    ideaId = magi.bus.actionItems.save({ category: "idea", content: "Optional dashboard idea; not a commitment." }).id;
    const inner = magi.bus.chats.forChannel("subconscious", "self");
    taskId = magi.bus.tasks.create({ name: "Review optional dashboard", prompt: "Review the optional dashboard idea privately.", cron: "0 0 1 1 *", run_at: null, chat_id: inner.id }).id;
    await magi.start();
    const job = messageDelivery.send(magi.bus, inner.id, "Reconcile recent context using the continuity skill.", undefined, "task");
    for (let i = 0; i < 500 && !magi.bus.board("MessageDeliveryJob").result(job); i++) await sleep(10);
    assert.equal(magi.bus.board("MessageDeliveryJob").result(job)?.status, "completed");
    assert.equal(stage, 5);
    assert.equal(magi.bus.actionItems.get(goalId)!.archived, false);
    assert.equal(magi.bus.actionItems.get(goalId)!.version, 2);
    assert.equal(magi.bus.actionItems.get(todoId)!.archived, true);
    assert.equal(magi.bus.actionItems.get(ideaId)!.archived, true);
    assert.equal(magi.bus.memoryBook.list().length, 1);
    assert.match(magi.bus.memoryBook.list()[0].detail, new RegExp(`message ${sourceId}`));
    assert.equal(magi.bus.tasks.list().find(task => task.id === taskId)!.enabled, false);
    assert.deepEqual(delivered, []);
    assert.equal(magi.bus.chats.list("subconscious").length, 1);
    assert.ok(magi.bus.messages.list(inner.id).some(message => message.contact_id === MAGI_CONTACT_ID && message.content.includes(`Processed through message ${cursor}`)));
  } finally { await magi.stop(); await rm(path, { recursive: true, force: true }); }
});

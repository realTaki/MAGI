import { afterEach, expect, test , sleep} from "./test.js";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Magi } from "../eva.js";
import { cronMatches } from "@magi/channel-tasks/worker.js";

const workspaces: string[] = [];
afterEach(async () => { for (const path of workspaces.splice(0)) await rm(path, { recursive: true, force: true }); });

test("the task worker carries schedule_task and a due task enters the agent", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-task-"));
  workspaces.push(workspace);
  const prompts: string[] = [];
  const delivered: string[] = [];
  const magi = new Magi("@task.magi", {
    workspace, deliver: (text) => delivered.push(text),
    client: { async complete(job) { prompts.push(job.messages.at(-1)?.content ?? ""); return { role: "assistant", content: "task complete" }; } },
  });
  try {
    // The tool travels with the task worker: offered exactly while that worker runs.
    expect(magi.bus.tools.get("schedule_task")).toBeNull();
    await magi.start();
    expect(magi.bus.tools.get("schedule_task")).not.toBeNull();

    const chat = magi.bus.chats.forChannel("cli", "terminal");
    const saved = await runTool(magi, "schedule_task", {
      name: "daily review", prompt: "Review the project", frequency: "daily", hour: 9, minute: 30,
      chat_id: chat.id,
    }) as { task_id: number; cron: string };
    expect(saved.cron).toBe("30 9 * * *");

    // Due now: every minute matches, so the worker's own scan fires it.
    magi.bus.tasks.save({ name: "daily review", prompt: "Review the project", cron: "* * * * *", chat_id: chat.id });
    for (let i = 0; i < 400 && !delivered.length; i++) await sleep(10);
    expect(magi.bus.tasks.get(saved.task_id)?.last_fired_minute).toBeDefined();
    expect(prompts.join("\n")).toContain("Review the project");
    expect(prompts.join("\n")).toContain("schedule: * * * * *");
    expect(delivered).toEqual(["task complete"]);
  } finally {
    await magi.stop();
  }
});

/** What the agent does with a call: publish it and wait for whichever worker owns the tool. */
async function runTool(magi: Magi, name: string, args: Record<string, unknown>): Promise<unknown> {
  const board = magi.bus.board("RunToolJob");
  const id = board.publish({ call: { tool_call_id: "call-1", name, arguments: args } }, "test");
  for (let i = 0; i < 200 && !board.result(id); i++) await sleep(10);
  const result = board.result(id);
  if (result?.status !== "completed") throw new Error(result?.error ?? `${name} did not complete`);
  return JSON.parse(result.output?.content ?? "null");
}

test("cron matching supports ranges, lists, and steps in UTC", () => {
  const date = new Date("2026-09-23T15:30:00Z");
  expect(cronMatches("*/15 9-17 * * 1-5", date)).toBeTrue();
  expect(cronMatches("0,15 9-17 * * 1-5", date)).toBeFalse();
  expect(cronMatches("30 15 * * 3", date)).toBeTrue();
  expect(cronMatches("0 8 * * 7", new Date("2026-09-27T08:00:00Z"))).toBeTrue();
});

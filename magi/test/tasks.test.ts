import { afterEach, expect, test , sleep} from "./test.js";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Magi } from "../magi.js";
import { cronMatches } from "../channels/tasks/worker.js";
import { builtinTools } from "../tools/registry.js";

const workspaces: string[] = [];
afterEach(async () => { for (const path of workspaces.splice(0)) await rm(path, { recursive: true, force: true }); });

test("schedule tool persists a task and manual trigger enters the agent", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-task-"));
  workspaces.push(workspace);
  const prompts: string[] = [];
  const delivered: string[] = [];
  const magi = new Magi("@task.magi", {
    workspace, deliver: (text) => delivered.push(text),
    client: { async complete(job) { prompts.push(job.messages.at(-1)?.content ?? ""); return { role: "assistant", content: "task complete" }; } },
  });
  const chat = magi.bus.chats.forChannel("cli", "terminal");
  const schedule = builtinTools(magi.bus).find((tool) => tool.name === "schedule_task")!;
  const saved = JSON.parse(await schedule.run({
    name: "daily review", prompt: "Review the project", frequency: "daily", hour: 9, minute: 30,
    chat_id: chat.id,
  })) as { task_id: number; cron: string };
  expect(saved.cron).toBe("30 9 * * *");

  await magi.start();
  const board = magi.bus.board("RunTaskNotify");
  const id = board.publish({ task_id: saved.task_id, manual: true }, "test");
  for (let i = 0; i < 200 && (!board.result(id) || !delivered.length); i++) await sleep(10);
  expect(board.result(id)).toMatchObject({ status: "completed" });
  expect(prompts.join("\n")).toContain("Review the project");
  expect(delivered).toEqual(["task complete"]);
  await magi.stop();
});

test("cron matching supports ranges, lists, and steps in UTC", () => {
  const date = new Date("2026-09-23T15:30:00Z");
  expect(cronMatches("*/15 9-17 * * 1-5", date)).toBeTrue();
  expect(cronMatches("0,15 9-17 * * 1-5", date)).toBeFalse();
  expect(cronMatches("30 15 * * 3", date)).toBeTrue();
  expect(cronMatches("0 8 * * 7", new Date("2026-09-27T08:00:00Z"))).toBeTrue();
});

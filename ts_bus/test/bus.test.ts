import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";

import { launchDemo } from "../demo/launcher.js";
import { Bus, CallLLMJob, slot } from "../src/index.js";

test("launcher attaches the provider and SettingsBook stays behind Jobs", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-ts-bus-"));
  try {
    const runtime = await launchDemo(workspace);
    assert.equal(runtime.provider.isAttached, true);
    assert.equal(runtime.provider.model, "demo-model");
    assert.equal(runtime.bus.forWorker("provider-2", runtime.provider.requiredSlots), null);
    await runtime.shutdown();
    assert.equal(runtime.provider.isAttached, false);
  } finally {
    await rm(workspace, { recursive: true, force: true });
  }
});

test("CallLLMJobBoard persists publish, claim, result, and reopen", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-ts-bus-"));
  try {
    const bus = await Bus.open(workspace);
    const caller = bus.forWorker("caller", [
      slot(CallLLMJob, "publish"),
      slot(CallLLMJob, "getResult"),
    ]);
    const provider = bus.forWorker("provider", [
      slot(CallLLMJob, "claim"),
      slot(CallLLMJob, "submitResult"),
    ]);
    assert.ok(caller && provider);

    const jobId = await caller.board(CallLLMJob).publish({
      messages: [{ role: "user", content: "hello" }],
    });
    const job = await provider.board(CallLLMJob).claim();
    assert.equal(job?.id, jobId);
    assert.equal(
      await provider.board(CallLLMJob).submitResult(jobId, {
        output: { text: "", model: "demo-model" },
      }),
      true,
    );

    const reopened = await Bus.open(workspace);
    const reader = reopened.forWorker("reader", [slot(CallLLMJob, "getResult")]);
    assert.ok(reader);
    assert.deepEqual(await reader.board(CallLLMJob).getResult(jobId), {
      id: jobId,
      status: "completed",
      output: { text: "", model: "demo-model" },
      error: undefined,
    });
    caller.detach();
    provider.detach();
    reader.detach();
    bus.close();
    reopened.close();
  } finally {
    await rm(workspace, { recursive: true, force: true });
  }
});

test("Firmware creates explicit SQLite tables", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-ts-bus-"));
  try {
    const bus = await Bus.open(workspace);
    bus.close();
    const database = new DatabaseSync(join(workspace, "memories", "magi.db"));
    const tables = database
      .prepare("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
      .all()
      .map((row) => row.name);
    const callLLMColumns = database
      .prepare("SELECT name FROM pragma_table_info('jobs_call_llm') ORDER BY cid")
      .all()
      .map((row) => row.name);
    const migrations = database
      .prepare("SELECT name FROM __drizzle_migrations ORDER BY name")
      .all()
      .map((row) => row.name);
    database.close();

    assert.deepEqual(tables, [
      "__drizzle_migrations",
      "books_settings",
      "jobs_call_llm",
      "jobs_get_setting",
      "sqlite_sequence",
    ]);
    assert.deepEqual(callLLMColumns, [
      "id",
      "publisher",
      "worker",
      "status",
      "error",
      "messages",
      "contact_id",
      "max_tokens",
      "tools",
      "text",
      "thinking",
      "tool_uses",
      "raw_blocks",
      "finish_reason",
      "model",
      "error_code",
      "created_at",
      "updated_at",
    ]);
    assert.deepEqual(migrations, ["0000_mute_jubilee.sql"]);
  } finally {
    await rm(workspace, { recursive: true, force: true });
  }
});

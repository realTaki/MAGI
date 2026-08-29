import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import Database from "better-sqlite3";
import test from "node:test";

import { Bus, CallLLMJob, slot } from "@magi/bus";
import { launchPlayground } from "../src/launcher.js";

test("launcher attaches isolated modules and SettingsBook stays behind Jobs", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "ts-magi-"));
  try {
    const runtime = await launchPlayground(workspace);
    assert.equal(runtime.caller.isAttached, true);
    assert.equal(runtime.provider.isAttached, true);
    assert.equal(runtime.reader.isAttached, true);
    assert.equal(runtime.provider.model, "demo-model");
    assert.equal(runtime.bus.forWorker("caller-2", runtime.caller.requiredSlots), null);
    await runtime.shutdown();
    assert.equal(runtime.caller.isAttached, false);
    assert.equal(runtime.provider.isAttached, false);
    assert.equal(runtime.reader.isAttached, false);
  } finally {
    await rm(workspace, { recursive: true, force: true });
  }
});

test("Caller, Provider, and ResultReader coordinate only through BUS", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "ts-magi-"));
  try {
    const runtime = await launchPlayground(workspace);
    const jobId = await runtime.caller.ask("hello");
    assert.equal(await runtime.reader.read(jobId), null);
    assert.equal(await runtime.provider.serveNext(), jobId);
    assert.deepEqual(await runtime.reader.read(jobId), {
      id: jobId,
      status: "completed",
      output: { text: "demo response to: hello", model: "demo-model" },
      error: undefined,
    });
    await runtime.shutdown();

    const reopened = await Bus.open(workspace);
    const reader = reopened.forWorker("reader", [slot(CallLLMJob, "getResult")]);
    assert.ok(reader);
    assert.deepEqual(await reader.board(CallLLMJob).getResult(jobId), {
      id: jobId,
      status: "completed",
      output: { text: "demo response to: hello", model: "demo-model" },
      error: undefined,
    });
    reader.detach();
    reopened.close();
  } finally {
    await rm(workspace, { recursive: true, force: true });
  }
});

test("Firmware creates explicit SQLite tables", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "ts-magi-"));
  try {
    const bus = await Bus.open(workspace);
    bus.close();
    const database = new Database(join(workspace, "memories", "magi.db"), { readonly: true });
    const tables = database
      .prepare("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
      .pluck()
      .all() as string[];
    const callLLMColumns = database
      .prepare("SELECT name FROM pragma_table_info('jobs_call_llm') ORDER BY cid")
      .pluck()
      .all() as string[];
    const migrations = database
      .prepare("SELECT hash, created_at FROM __drizzle_migrations ORDER BY created_at")
      .all() as Array<{ hash: string; created_at: number }>;
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
    assert.equal(migrations.length, 1);
    assert.match(migrations[0].hash, /^[a-f0-9]{64}$/);
    assert.equal(migrations[0].created_at, 1787971490481);
  } finally {
    await rm(workspace, { recursive: true, force: true });
  }
});

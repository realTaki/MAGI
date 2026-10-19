import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { ASP_MAGI_HANDLE, EVA_MAGI_HANDLE, SHELL_MAGI_HANDLE } from "@magi/bus";
import { ASP_ORIGIN } from "@magi/webapp";
import { Shell } from "@magi/node";
import { Asp } from "../../asp/asp.ts";

const repository = path.resolve(import.meta.dirname, "../../..");
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

test("a new Shell starts its local Melchior launcher before naming the default society", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "magi-cold-start-"));
  const oldHome = process.env.MAGI_HOME;
  const oldOsHome = process.env.MAGI_OS_HOME;
  const oldOrigin = ASP_ORIGIN.href;
  process.env.MAGI_HOME = path.join(root, "profile");
  process.env.MAGI_OS_HOME = path.join(root, "os-home");
  const asp = new Asp({ databasePath: path.join(root, "asp.sqlite"), workspace: path.join(root, "asp"),
    host: "127.0.0.1", port: 0,
    client: { async complete() { return { role: "assistant" as const, content: "NO_REPLY" }; } },
  });
  const launched: ReturnType<typeof spawn>[] = [];
  let shell: Shell | undefined;
  try {
    await asp.start();
    ASP_ORIGIN.href = asp.origin;
    shell = new Shell({
      paths: { home: process.env.MAGI_OS_HOME, userData: path.join(root, "electron"), checkout: repository },
      repository: "https://github.com/realTaki/MAGI.git", managed: false,
      tools: { git: "git", node: process.execPath, npm: path.join(repository, "apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js"), env: process.env },
      emit: () => {}, openExternal: async () => {}, copy: () => {},
      spawn(file, args, options) {
        assert.equal(args[0], path.join(repository, "apps/eva/eva.mjs"));
        assert.deepEqual(args.slice(1), ["--role", "agent", EVA_MAGI_HANDLE]);
        assert.equal(options.env.MAGI_HOST, "0");
        const child = spawn(file, args, options);
        launched.push(child);
        return child;
      },
    });
    await shell.start();
    const startedAt = Date.now();
    await shell.worker.methods.start();
    console.log(`cold start completed in ${Date.now() - startedAt}ms`);
    assert.ok(Date.now() - startedAt < 15_000, "startup must not wait for the 30-second offline naming timeout");
    assert.equal(launched.length, 1, "only the explicitly seeded local agent gets a launcher");
    for (const [handle, nickname] of [[SHELL_MAGI_HANDLE, "BALTHASAR"], [ASP_MAGI_HANDLE, "CASPER"], [EVA_MAGI_HANDLE, "MELCHIOR"]]) {
      assert.equal(asp.transport.isOnline(handle), true, handle);
      assert.equal(asp.store.getAgent(handle)?.nickname, nickname);
    }
    await shell.worker.methods.start();
    assert.equal(launched.length, 1, "repeated startup reuses the online node");
    const chats = asp.server.operator.listChats(asp.operatorHandle);
    assert.equal(chats.filter((chat) => (chat.agents as string[])?.includes(EVA_MAGI_HANDLE)).length, 1);
  } finally {
    await shell?.stop(true);
    let stopped = true;
    for (const child of launched) {
      for (let attempt = 0; attempt < 300 && child.exitCode === null && child.signalCode === null; attempt++) await sleep(50);
      if (child.exitCode === null && child.signalCode === null) { stopped = false; child.kill("SIGKILL"); }
    }
    await asp.stop();
    ASP_ORIGIN.href = oldOrigin;
    if (oldHome === undefined) delete process.env.MAGI_HOME; else process.env.MAGI_HOME = oldHome;
    if (oldOsHome === undefined) delete process.env.MAGI_OS_HOME; else process.env.MAGI_OS_HOME = oldOsHome;
    await rm(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
    assert.ok(stopped, "the local launcher must exit when Shell shuts down");
  }
});

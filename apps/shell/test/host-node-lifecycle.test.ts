import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { tmpdir } from "node:os";
import { ASP_MAGI_HANDLE, SHELL_MAGI_HANDLE, EVA_MAGI_HANDLE } from "@magi/bus";
import { ASP_ORIGIN } from "@magi/webapp";
import { Shell } from "@magi/node";
import { Asp } from "../../asp/asp.ts";

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const repository = path.resolve(import.meta.dirname, "../../..");
async function until(read: () => boolean | Promise<boolean>, timeout = 8000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { if (await read()) return; await sleep(20); }
  throw new Error("actual node lifecycle did not converge");
}

test("profile controls pause hosted Shell/Casper durably and stop/restart the real local Melchior launcher", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "magi-host-control-"));
  const previous = { home: process.env.MAGI_HOME, os: process.env.MAGI_OS_HOME, origin: ASP_ORIGIN.href };
  process.env.MAGI_HOME = path.join(root, "profile");
  process.env.MAGI_OS_HOME = path.join(root, "os");
  const asp = new Asp({ databasePath: path.join(root, "asp.sqlite"), workspace: path.join(root, "casper"), host: "127.0.0.1", port: 0,
    client: { async complete() { return { role: "assistant" as const, content: "NO_REPLY" }; } },
  });
  const children: ReturnType<typeof spawn>[] = [];
  let shell: Shell | undefined;
  let entered = false;
  let requestSignal: AbortSignal | undefined;
  let finish!: (value: any) => void;
  const pending = new Promise<any>((resolve) => { finish = resolve; });
  try {
    await asp.start(); ASP_ORIGIN.href = asp.origin;
    shell = new Shell({
      paths: { home: process.env.MAGI_OS_HOME, userData: path.join(root, "electron"), checkout: repository },
      repository: "https://github.com/realTaki/MAGI.git", managed: false,
      tools: { git: "git", node: process.execPath, npm: path.join(repository, "apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js"), env: process.env },
      emit: () => {}, openExternal: async () => {}, copy: () => {},
      client: { complete(_job, signal) { entered = true; requestSignal = signal; return pending; } },
      spawn(file, args, options) { const child = spawn(file, args, options); children.push(child); return child; },
    });
    await shell.start();
    const api = shell.worker.methods;
    await api.start();
    await until(() => [SHELL_MAGI_HANDLE, ASP_MAGI_HANDLE, EVA_MAGI_HANDLE].every((handle) => asp.transport.isOnline(handle)));
    for (const handle of [SHELL_MAGI_HANDLE, ASP_MAGI_HANDLE, EVA_MAGI_HANDLE]) assert.equal((await api["magi.info"]({ handle })).running, true, handle);

    const stopShell = await api["magi.stop"]({ handle: SHELL_MAGI_HANDLE });
    assert.equal(stopShell.stopped, true);
    await until(() => !asp.transport.isOnline(SHELL_MAGI_HANDLE));
    assert.equal((await api["magi.info"]({ handle: SHELL_MAGI_HANDLE })).online, false);
    assert.equal((await api["magi.info"]({ handle: SHELL_MAGI_HANDLE })).running, false);
    assert.equal(asp.transport.isOnline(ASP_MAGI_HANDLE), true);
    assert.equal(asp.transport.isOnline(EVA_MAGI_HANDLE), true);
    // A worker-start request and multiple supervisor health cycles must not undo an operator stop.
    const wake = shell.bus.board("ManageWorkerNotify").publish({ worker: "asp", action: "start" }, "test");
    await until(() => shell!.bus.board("ManageWorkerNotify").result(wake) !== null);
    await sleep(1300);
    assert.equal(asp.transport.isOnline(SHELL_MAGI_HANDLE), false);
    assert.equal(shell.bus.settings.get("node.enabled"), "false");
    await api.ready(); // The UI backend itself is still callable while Balthasar is stopped.

    for (const method of ["GET", "POST"]) {
      const denied = await fetch(new URL("/runtime/host", asp.origin), { method, headers: { "content-type": "application/json" }, ...(method === "POST" ? { body: JSON.stringify({ action: "stop" }) } : {}) });
      assert.equal(denied.status, 401, "host controls require operator authentication");
    }
    await api["magi.stop"]({ handle: ASP_MAGI_HANDLE });
    await until(() => !asp.transport.isOnline(ASP_MAGI_HANDLE));
    assert.equal((await api["magi.info"]({ handle: ASP_MAGI_HANDLE })).running, false);
    assert.equal((await (await fetch(new URL("/health", asp.origin))).json()).status, "ok", "stopping Casper keeps the relay alive");
    assert.equal(asp.transport.isOnline(EVA_MAGI_HANDLE), true);
    asp.bus.board("ManageWorkerNotify").publish({ worker: "asp", action: "start" }, "test");
    await sleep(1300);
    assert.equal(asp.transport.isOnline(ASP_MAGI_HANDLE), false);

    await api["magi.start"]({ handle: SHELL_MAGI_HANDLE });
    await api["magi.start"]({ handle: ASP_MAGI_HANDLE });
    await until(() => asp.transport.isOnline(SHELL_MAGI_HANDLE) && asp.transport.isOnline(ASP_MAGI_HANDLE));
    assert.equal((await api["magi.start"]({ handle: SHELL_MAGI_HANDLE })).started, false, "repeated host start is idempotent");

    const heartbeat = path.join(root, "owned-command.json");
    const code = `const fs=require('node:fs');let count=0;const beat=()=>fs.writeFileSync(${JSON.stringify(heartbeat)},JSON.stringify({pid:process.pid,count:++count}));beat();setInterval(beat,50)`;
    const quote = (value: string) => process.platform === "win32" ? "'" + value.replaceAll("'", "''") + "'" : "'" + value.replaceAll("'", "'\"'\"'") + "'";
    const command = `${process.platform === "win32" ? "& " : ""}${quote(process.execPath)} -e ${quote(code)}`;
    const toolId = shell.bus.board("RunToolJob").publish({ call: { tool_call_id: "owned-command", name: "bash", arguments: { command } } }, "test");
    await until(() => existsSync(heartbeat));
    const room = shell.bus.chats.list("asp")[0];
    await fetch(new URL(`/chats/${room.delivery_address}/messages`, asp.origin), { method: "POST", headers: { authorization: `Bearer ${asp.operatorToken}`, "content-type": "application/json" }, body: JSON.stringify({ content: "synthetic in-flight request" }) });
    await until(() => entered);
    const beforeStop = Date.now();
    await api["magi.stop"]({ handle: SHELL_MAGI_HANDLE });
    assert.ok(Date.now() - beforeStop < 3000, "Stop cancels an active model request instead of waiting for a provider timeout");
    assert.equal(requestSignal?.aborted, true);
    const lastHeartbeat = readFileSync(heartbeat, "utf8");
    await sleep(200);
    assert.equal(readFileSync(heartbeat, "utf8"), lastHeartbeat, "a real foreground command cannot keep executing after node Stop");
    assert.equal(shell.bus.board("RunToolJob").result(toolId)?.status, "failed");
    finish({ role: "assistant", content: "late synthetic result must not be delivered" });
    await sleep(50);
    assert.ok(!shell.bus.messages.list(room.id).some((row) => row.content.includes("late synthetic result")));

    const first = children[0];
    assert.ok(first?.pid && first.exitCode === null);
    assert.equal((await api["magi.stop"]({ handle: EVA_MAGI_HANDLE })).stopped, true);
    assert.ok(first.exitCode !== null || first.signalCode !== null, "the actual launcher exited before Stop returned");
    await until(() => !asp.transport.isOnline(EVA_MAGI_HANDLE));
    assert.equal((await api["magi.info"]({ handle: EVA_MAGI_HANDLE })).running, false);
    await api.start();
    assert.equal(children.length, 1, "ordinary startup/prepare cannot revive an explicitly stopped local node");
    assert.equal(asp.transport.isOnline(SHELL_MAGI_HANDLE), false);
    await api["magi.start"]({ handle: EVA_MAGI_HANDLE });
    await until(() => asp.transport.isOnline(EVA_MAGI_HANDLE));
    assert.equal(children.length, 2);
    assert.notEqual(children[1].pid, first.pid);
    assert.equal((await api["magi.info"]({ handle: EVA_MAGI_HANDLE })).running, true);
  } finally {
    finish({ role: "assistant", content: "NO_REPLY" });
    await shell?.worker.methods["magi.stop"]({ handle: SHELL_MAGI_HANDLE }).catch(() => {});
    await shell?.stop(true);
    for (const child of children) {
      for (let i = 0; i < 200 && child.exitCode === null && child.signalCode === null; i++) await sleep(25);
      if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
    }
    await asp.stop(); ASP_ORIGIN.href = previous.origin;
    if (previous.home === undefined) delete process.env.MAGI_HOME; else process.env.MAGI_HOME = previous.home;
    if (previous.os === undefined) delete process.env.MAGI_OS_HOME; else process.env.MAGI_OS_HOME = previous.os;
    await rm(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
});

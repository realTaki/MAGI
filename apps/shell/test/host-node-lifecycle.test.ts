import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { tmpdir } from "node:os";
import { Shell } from "@magi/node";

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
async function until(read: () => boolean) {
  const end = Date.now() + 8000;
  while (Date.now() < end) { if (read()) return; await sleep(20); }
  throw new Error("lifecycle did not converge");
}
const repository = path.resolve(import.meta.dirname, "../../..");
test("Shell explicitly creates, stops and restarts a real owned EVA without an ASP relay", { timeout: 45_000 }, async () => {
  const root = await mkdtemp(path.join(tmpdir(), "magi-owned-peer-"));
  const prior = { home: process.env.MAGI_HOME, os: process.env.MAGI_OS_HOME };
  process.env.MAGI_HOME = path.join(root, "profile"); process.env.MAGI_OS_HOME = path.join(root, "os");
  const children: ReturnType<typeof spawn>[] = [];
  let entered = false;
  let requestSignal: AbortSignal | undefined;
  let finish!: (value: any) => void;
  const pending = new Promise<any>(resolve => { finish = resolve; });
  const shell = new Shell({ paths: { home: process.env.MAGI_OS_HOME, userData: path.join(root, "electron"), checkout: repository }, repository: "https://github.com/realTaki/MAGI.git", managed: false,
    tools: { git: "git", node: process.execPath, npm: path.join(repository, "apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js"), env: process.env },
    client: { complete(_job, signal) { entered = true; requestSignal = signal; return pending; } },
    emit() {}, async openExternal() {}, copy() {}, spawn(file, args, options) { const child = spawn(file, args, options); children.push(child); return child; } });
  try {
    await shell.start(); assert.equal(children.length, 0);
    const api = shell.worker.methods;
    const create = api.localManagement.find(tool => tool.name === "create_local_eva")!;
    const result = JSON.parse(await create.run({ name: "owned-one" }));
    assert.equal(result.ready, true); assert.equal(children.length, 1);
    let row = api["peer.contacts"]().find(contact => contact.id === String(result.contact_id));
    assert.equal(row?.handle, "@owned-one.magi"); assert.equal(row?.running, true);
    assert.equal((await api["peer.profile"]({ id: String(result.contact_id) })).owner, true);
    const lifecycle = api.localManagement.find(tool => tool.name === "local_eva_lifecycle")!;
    assert.equal(JSON.parse(await lifecycle.run({ contact_id: result.contact_id, action: "stop" })).stopped, true);
    assert.equal(api["peer.contacts"]().find(contact => contact.id === String(result.contact_id))?.running, false);
    await lifecycle.run({ contact_id: result.contact_id, action: "start" });
    row = api["peer.contacts"]().find(contact => contact.id === String(result.contact_id));
    assert.equal(row?.running, true); assert.equal(children.length, 2);
    await assert.rejects(lifecycle.run({ contact_id: 99999, action: "start" }), /not a locally provisioned/);
    // Owner provenance is durable; an ordinary remote peer cannot launch this tool.
    await api["peer.send"]({ id: "shell", content: "synthetic in-flight owner request" });
    await until(() => entered);
    const room = shell.bus.chats.list("shell-owner")[0];
    const message = shell.bus.messages.list(room.id).find(item => item.content === "synthetic in-flight owner request")!;
    const heartbeat = path.join(root, "owned-command.json");
    const script = path.join(root, "owned heartbeat.cjs");
    writeFileSync(script, `const fs=require('node:fs');let count=0;const beat=()=>fs.writeFileSync(${JSON.stringify(heartbeat)},JSON.stringify({pid:process.pid,count:++count}));beat();setInterval(beat,50)`);
    const quote = (value: string) => "'" + value.replaceAll("'", process.platform === "win32" ? "''" : "'\"'\"'") + "'";
    const toolId = shell.bus.board("RunToolJob").publish({ chat_id: room.id, message_id: message.id,
      call: { tool_call_id: "owned-command", name: "bash", arguments: { command: `${process.platform === "win32" ? "& " : ""}${quote(process.execPath)} ${quote(script)}` } } }, "test");
    await until(() => existsSync(heartbeat));
    const before = Date.now();
    await api["peer.send"]({ id: "shell", content: "", metadata: { local_eva_action: { contact_id: "shell", action: "stop" } } });
    assert.ok(Date.now() - before < 3000, "operator stop cancels an active model request promptly");
    assert.equal(requestSignal?.aborted, true);
    const lastHeartbeat = readFileSync(heartbeat, "utf8");
    await sleep(200);
    assert.equal(readFileSync(heartbeat, "utf8"), lastHeartbeat, "foreground command stops with the node");
    assert.equal(shell.bus.board("RunToolJob").result(toolId)?.status, "failed");
    finish({ role: "assistant", content: "late synthetic result must not be delivered" });
    await sleep(50);
    assert.ok(!shell.bus.messages.list(room.id).some(row => row.content.includes("late synthetic result")));
  } finally {
    finish({ role: "assistant", content: "NO_REPLY" });
    await shell.stop(true);
    for (const child of children) assert.ok(child.exitCode !== null || child.signalCode !== null, "owned launcher exits on Shell shutdown");
    for (const [key, value] of [["MAGI_HOME", prior.home], ["MAGI_OS_HOME", prior.os]]) { if (value === undefined) delete process.env[key!]; else process.env[key!] = value; }
    await rm(root, { recursive: true, force: true });
  }
});

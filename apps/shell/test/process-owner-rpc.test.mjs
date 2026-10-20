import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import test from "node:test";
import { createSupervisor } from "../process.mjs";

function fixture() {
  const spawned = [];
  const owner = {
    assertOpen() {}, beginClose() {}, async close() {}, async reap() {},
    async signal(child) { child.emit("exit", 0, null); },
    spawn(_node, _args, options) {
      const child = new EventEmitter();
      Object.assign(child, { stdin: new PassThrough(), stdout: new PassThrough(), stderr: new PassThrough(), connected: true, send() {} });
      spawned.push({ child, options });
      setImmediate(() => { child.emit("spawn"); child.emit("message", { type: "ready" }); });
      return child;
    },
  };
  const supervisor = createSupervisor({ node: process.execPath, env: {}, owner, readyTimeoutMs: 100 });
  let sequence = 0;
  function request(child, method, params) {
    const id = `host-request-${++sequence}`;
    return new Promise((resolve, reject) => {
      const listener = (chunk) => {
        const message = JSON.parse(String(chunk));
        if (message.id !== id) return;
        child.stdin.off("data", listener);
        if (message.type === "err") reject(new Error(message.error));
        else resolve(message.result);
      };
      child.stdin.on("data", listener);
      child.stdout.write(JSON.stringify({ type: "req", id, method, params }) + "\n");
    });
  }
  const spec = { cwd: "/fixture/apps/eva", entry: "/fixture/apps/eva/eva.mjs", args: ["--role", "agent", "@local.magi"], env: { MAGI_HOST: "1", MAGI_SERVER: "1", MAGI_ROLE: "agent", MAGI_HANDLE: "@local.magi" } };
  return { supervisor, spawned, request, spec };
}

test("desktop forwards only the managed local EVA owner method whitelist", async (t) => {
  const { supervisor, spawned, request, spec } = fixture();
  t.after(() => supervisor.close());
  const host = await supervisor.start({ cwd: "/fixture", entry: "/fixture/shell.mjs" }, { host: {} });
  await host.whenReady();
  const local = await supervisor.start(spec); await local.whenReady();
  const hostChild = spawned[0].child, evaChild = spawned[1].child;
  assert.equal(spawned[1].options.stdio[0], "pipe");
  const received = [];
  evaChild.stdin.on("data", (chunk) => {
    const message = JSON.parse(String(chunk));
    if (message.type !== "req") return;
    received.push(message);
    evaChild.stdout.write(JSON.stringify({ type: "res", id: message.id, result: { content: message.params?.content ?? "events", owner: true } }) + "\n");
  });
  assert.deepEqual(await request(hostChild, "process.invoke", { id: local.id, method: "owner.chat", params: { content: "hello" } }), { content: "hello", owner: true });
  assert.equal(received[0].method, "owner.chat");
  for (const method of ["owner.events", "owner.profile"]) await request(hostChild, "process.invoke", { id: local.id, method });
  for (const method of ["command", "provider.save", "spawn", "__proto__"]) await assert.rejects(request(hostChild, "process.invoke", { id: local.id, method }), /not allowed/);
  await assert.rejects(request(hostChild, "process.invoke", { id: host.id, method: "owner.profile" }), /unavailable/);
  await assert.rejects(request(hostChild, "process.invoke", { id: "https://remote.example", method: "owner.profile" }), /unavailable/);
  await assert.rejects(request(evaChild, "spawn", spec), /cannot invoke desktop/);
});

test("mismatched identity and non-EVA children never gain an owner bridge", async (t) => {
  const { supervisor, spawned, request, spec } = fixture();
  t.after(() => supervisor.close());
  const host = await supervisor.start({ cwd: "/fixture", entry: "/fixture/shell.mjs" }, { host: {} }); await host.whenReady();
  for (const candidate of [
    { ...spec, env: { ...spec.env, MAGI_HANDLE: "@other.magi" } },
    { ...spec, entry: "/fixture/apps/eva/untrusted.mjs" },
    { ...spec, args: ["--role", "agent", "@shell.magi"], env: { ...spec.env, MAGI_HANDLE: "@shell.magi" } },
  ]) {
    const child = await supervisor.start(candidate); await child.whenReady();
    assert.equal(spawned.at(-1).options.stdio[0], "ignore");
    await assert.rejects(request(spawned[0].child, "process.invoke", { id: child.id, method: "owner.profile" }), /unavailable/);
  }
});

test("owner requests reject on timeout and child exit instead of leaking pending promises", async (t) => {
  const { supervisor, spawned, request, spec } = fixture();
  t.after(() => supervisor.close());
  const host = await supervisor.start({ cwd: "/fixture", entry: "/fixture/shell.mjs" }, { host: {} }); await host.whenReady();
  const local = await supervisor.start(spec); await local.whenReady();
  await assert.rejects(request(spawned[0].child, "process.invoke", { id: local.id, method: "owner.events" }), /timed out/);
  const pending = request(spawned[0].child, "process.invoke", { id: local.id, method: "owner.profile" });
  const rejected = assert.rejects(pending, /process exited/);
  spawned[1].child.emit("exit", 1, null);
  await rejected;
  await assert.rejects(request(spawned[0].child, "process.invoke", { id: local.id, method: "owner.profile" }), /unavailable/);
});

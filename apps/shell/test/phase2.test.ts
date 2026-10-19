import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

import { Bus, messageDelivery } from "@magi/bus";
import { magiHome } from "@magi/runtime/home.js";
import { ManagerWorker } from "@magi/manager";
import { Asp } from "../../asp/asp.ts";
import { Magi } from "../../eva/eva.ts";
import { createSupervisor } from "../process.mjs";

const repo = path.resolve(import.meta.dirname, "../../..");
const tsc = path.join(repo, "node_modules/typescript/lib/tsc.js");
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function until(label: string, ready: () => boolean | Promise<boolean>, timeoutMs = 20_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await ready()) return;
    await sleep(30);
  }
  throw new Error(label);
}

function cards(bus: Bus): string[] {
  return bus.chats.list().flatMap((chat) => bus.messages.list(chat.id, 50).map((message) => message.content))
    .filter((content) => content === "card: Provider");
}

function writeBrokenApp(root: string, app: "eva" | "asp" | "shell", script: string): string {
  const checkout = path.join(root, "checkout");
  const appDir = path.join(checkout, "apps", app);
  mkdirSync(path.join(appDir, "dist"), { recursive: true });
  mkdirSync(path.join(appDir, "src"), { recursive: true });
  const entry = `${app}.js`;
  writeFileSync(path.join(appDir, "dist", entry), "export const version = 1;\n");
  writeFileSync(path.join(appDir, "dist", "extra.js"), "export const extra = 1;\n");
  writeFileSync(path.join(appDir, "src", "broken.ts"), "export const version = ;\n");
  writeFileSync(path.join(appDir, "tsconfig.json"), JSON.stringify({
    compilerOptions: { module: "nodenext", moduleResolution: "nodenext", target: "esnext", outDir: "dist", noEmit: true },
    files: ["src/broken.ts"],
  }));
  writeFileSync(path.join(appDir, "build.js"), `import { rmSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
writeFileSync("dist/${entry}", "function (");
rmSync("dist/extra.js", { force: true });
const result = spawnSync(process.execPath, [${JSON.stringify(tsc)}, "--pretty", "false", "-p", "tsconfig.json"], { stdio: "inherit" });
process.exit(result.status === 0 ? 1 : (result.status ?? 1));
`);
  writeFileSync(path.join(appDir, "package.json"), JSON.stringify({ type: "module", scripts: { [script]: "node build.js" } }));
  return checkout;
}

async function expectRollback(bus: Bus, checkout: string, app: "eva" | "asp" | "shell"): Promise<void> {
  const chat = bus.chats.forChannel("cli", "operator");
  messageDelivery.setHomeChat(bus, chat.id);
  const id = bus.board("ReplaceSelfJob").publish({ checkout, swap: false }, "test");
  await until("replace did not finish", () => bus.board("ReplaceSelfJob").result(id) !== null, 60_000);
  const result = bus.board("ReplaceSelfJob").result(id);
  assert.equal(result?.status, "failed");
  assert.equal(typeof result?.error, "string");
  const dist = path.join(checkout, "apps", app, "dist");
  assert.equal(readFileSync(path.join(dist, `${app}.js`), "utf8"), "export const version = 1;\n");
  assert.equal(readFileSync(path.join(dist, "extra.js"), "utf8"), "export const extra = 1;\n");
  const notes = bus.messages.list(chat.id).map((message) => message.content);
  assert.ok(notes.some((text) => text.startsWith("[replace] ") && text !== "[replace] replaced"));
}

test("a fresh MAGI shows a card and the operator can reopen it after cancellation without a model", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-card-"));
  const asp = new Asp({
    databasePath: path.join(root, "asp.sqlite"),
    workspace: path.join(root, "casper"),
    host: "127.0.0.1",
    port: 0,
    client: { async complete() { return { role: "assistant" as const, content: "NO_REPLY" }; }, async verify() { throw new Error("rejected"); } },
  });
  let calls = 0;
  let magi: Magi | null = null;
  try {
    await asp.start();
    const created = await fetch(new URL("/chats", asp.origin), {
      method: "POST",
      headers: { authorization: `Bearer ${asp.operatorToken}`, "content-type": "application/json" },
      body: JSON.stringify({ kind: "bot" }),
    });
    const body = await created.json() as { magi?: { handle?: string; token?: string } };
    const handle = body.magi?.handle ?? "";
    const token = body.magi?.token ?? "";
    magi = new Magi(handle, {
      workspace: path.join(root, "receiver"),
      asp: { base: asp.origin, token },
      client: {
        async complete() { calls += 1; return { role: "assistant" as const, content: "NO_REPLY" }; },
        async verify() { throw new Error("rejected"); },
      },
    });
    assert.equal(magi.bus.chats.list().length, 0);
    await magi.start();
    await until("provider card was not shown", () => cards(magi!.bus).length >= 1);
    assert.equal(calls, 0);
    assert.equal(magi.bus.secrets.get("provider.api_key"), null);
    const chat = magi.bus.chats.list("asp").find((candidate) =>
      magi!.bus.messages.list(candidate.id).some((message) => message.content === "card: Provider"));
    assert.ok(chat?.delivery_address);
    const address = chat.delivery_address;
    let card = "";
    await until("provider card was not delivered", async () => {
      const events = await fetch(new URL(`/chats/${address}/events`, asp.origin), {
        headers: { authorization: `Bearer ${asp.operatorToken}` },
      });
      if (!events.ok) return false;
      const listed = await events.json() as { events?: Array<{ payload?: { metadata?: { card?: { id?: string } } } }> };
      const id = listed.events?.map((event) => event.payload?.metadata?.card?.id).find((value) => typeof value === "string");
      if (!id) return false;
      card = id;
      return true;
    });
    const joined = await fetch(new URL(`/chats/${address}/join`, asp.origin), {
      method: "POST",
      headers: { authorization: `Bearer ${asp.operatorToken}` },
    });
    assert.equal(joined.ok, true);
    const submitted = await fetch(new URL(`/chats/${address}/messages`, asp.origin), {
      method: "POST",
      headers: { authorization: `Bearer ${asp.operatorToken}`, "content-type": "application/json" },
      body: JSON.stringify({ content: "card rejected", metadata: { card_reject: { id: card } } }),
    });
    assert.equal(submitted.ok, true);
    await until("cancellation was not acknowledged", () => magi!.bus.settings.get(`card.receipt.${card}`) !== null);
    const reopened = await fetch(new URL(`/chats/${address}/messages`, asp.origin), {
      method: "POST",
      headers: { authorization: `Bearer ${asp.operatorToken}`, "content-type": "application/json" },
      body: JSON.stringify({ content: "open provider", metadata: { card_result: { id: `open-${card}`, action: "open" } } }),
    });
    assert.equal(reopened.ok, true);
    await until("provider card was not opened", () => cards(magi!.bus).length >= 2);
    assert.equal(cards(magi.bus).length, 2);
    let again = "";
    await until("the card was not sent a second time", async () => {
      const events = await fetch(new URL(`/chats/${address}/events`, asp.origin), {
        headers: { authorization: `Bearer ${asp.operatorToken}` },
      });
      if (!events.ok) return false;
      const listed = await events.json() as { events?: Array<{ payload?: { metadata?: { card?: { id?: string } } } }> };
      const ids = listed.events?.map((event) => event.payload?.metadata?.card?.id).filter((value): value is string => typeof value === "string") ?? [];
      const next = ids.find((value) => value !== card);
      if (!next) return false;
      again = next;
      return true;
    });
    const againSubmitted = await fetch(new URL(`/chats/${address}/messages`, asp.origin), {
      method: "POST",
      headers: { authorization: `Bearer ${asp.operatorToken}`, "content-type": "application/json" },
      body: JSON.stringify({ content: "card rejected", metadata: { card_reject: { id: again } } }),
    });
    assert.equal(againSubmitted.ok, true);
    await until("second cancellation was not acknowledged", () => magi!.bus.settings.get(`card.receipt.${again}`) !== null);
    assert.equal(magi.bus.settings.get("provider.card.stopped"), "1");
    const workspace = path.join(root, "receiver");
    await magi.stop();
    magi = new Magi(handle, {
      workspace,
      asp: { base: asp.origin, token },
      client: {
        async complete() { calls += 1; return { role: "assistant" as const, content: "NO_REPLY" }; },
        async verify() { throw new Error("rejected"); },
      },
    });
    await magi.start();
    await sleep(400);
    assert.equal(cards(magi.bus).length, 2);
    assert.equal(magi.bus.settings.get("provider.card.stopped"), "1");
    assert.equal(calls, 0);
    assert.equal(magi.bus.secrets.get("provider.api_key"), null);
  } finally {
    await magi?.stop();
    await asp.stop();
    rmSync(root, { recursive: true, force: true });
  }
});

test("two restarts do not count as rejecting the provider card", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-card-restart-"));
  const asp = new Asp({
    databasePath: path.join(root, "asp.sqlite"),
    workspace: path.join(root, "casper"),
    host: "127.0.0.1",
    port: 0,
    client: { async complete() { return { role: "assistant" as const, content: "NO_REPLY" }; } },
  });
  let calls = 0;
  let magi: Magi | null = null;
  const client = {
    async complete() { calls += 1; return { role: "assistant" as const, content: "NO_REPLY" }; },
  };
  try {
    await asp.start();
    const created = await fetch(new URL("/chats", asp.origin), {
      method: "POST",
      headers: { authorization: `Bearer ${asp.operatorToken}`, "content-type": "application/json" },
      body: JSON.stringify({ kind: "bot" }),
    });
    const body = await created.json() as { magi?: { handle?: string; token?: string } };
    const handle = body.magi?.handle ?? "";
    const token = body.magi?.token ?? "";
    const workspace = path.join(root, "receiver");
    magi = new Magi(handle, { workspace, asp: { base: asp.origin, token }, client });
    await magi.start();
    await until("provider card was not shown", () => cards(magi!.bus).length >= 1);
    assert.equal(calls, 0);
    for (let restart = 0; restart < 2; restart += 1) {
      const before = cards(magi.bus).length;
      await magi.stop();
      magi = new Magi(handle, { workspace, asp: { base: asp.origin, token }, client });
      await magi.start();
      await until("provider card was not shown after a restart", () => cards(magi!.bus).length > before);
    }
    await sleep(400);
    const texts = magi.bus.chats.list().flatMap((chat) => magi!.bus.messages.list(chat.id, 50).map((message) => message.content));
    assert.equal(texts.some((text) => text.includes("rejected") || text.includes("will not be shown again")), false);
    assert.equal(magi.bus.settings.get("provider.card.offers"), null);
    assert.equal(magi.bus.settings.get("provider.card.rejections") ?? "0", "0");
    assert.equal(calls, 0);
  } finally {
    await magi?.stop();
    await asp.stop();
    rmSync(root, { recursive: true, force: true });
  }
});

test("an explicitly reopened card survives a restart after cancellation", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-card-reject-restart-"));
  const asp = new Asp({
    databasePath: path.join(root, "asp.sqlite"),
    workspace: path.join(root, "casper"),
    host: "127.0.0.1",
    port: 0,
    client: { async complete() { return { role: "assistant" as const, content: "NO_REPLY" }; } },
  });
  let magi: Magi | null = null;
  const client = { async complete() { return { role: "assistant" as const, content: "NO_REPLY" }; } };
  try {
    await asp.start();
    const created = await fetch(new URL("/chats", asp.origin), {
      method: "POST",
      headers: { authorization: `Bearer ${asp.operatorToken}`, "content-type": "application/json" },
      body: JSON.stringify({ kind: "bot" }),
    });
    const body = await created.json() as { magi?: { handle?: string; token?: string } };
    const handle = body.magi?.handle ?? "";
    const token = body.magi?.token ?? "";
    const workspace = path.join(root, "receiver");
    magi = new Magi(handle, { workspace, asp: { base: asp.origin, token }, client });
    await magi.start();
    await until("provider card was not shown", () => cards(magi!.bus).length >= 1);
    const chat = magi.bus.chats.list("asp").find((candidate) =>
      magi!.bus.messages.list(candidate.id).some((message) => message.content === "card: Provider"));
    assert.ok(chat?.delivery_address);
    const address = chat.delivery_address;
    let card = "";
    await until("provider card was not delivered", async () => {
      const events = await fetch(new URL(`/chats/${address}/events`, asp.origin), {
        headers: { authorization: `Bearer ${asp.operatorToken}` },
      });
      if (!events.ok) return false;
      const listed = await events.json() as { events?: Array<{ payload?: { metadata?: { card?: { id?: string } } } }> };
      const id = listed.events?.map((event) => event.payload?.metadata?.card?.id).find((value) => typeof value === "string");
      if (!id) return false;
      card = id;
      return true;
    });
    const joined = await fetch(new URL(`/chats/${address}/join`, asp.origin), {
      method: "POST",
      headers: { authorization: `Bearer ${asp.operatorToken}` },
    });
    assert.equal(joined.ok, true);
    const submitted = await fetch(new URL(`/chats/${address}/messages`, asp.origin), {
      method: "POST",
      headers: { authorization: `Bearer ${asp.operatorToken}`, "content-type": "application/json" },
      body: JSON.stringify({ content: "card rejected", metadata: { card_reject: { id: card } } }),
    });
    assert.equal(submitted.ok, true);
    await until("cancellation was not acknowledged", () => magi!.bus.settings.get(`card.receipt.${card}`) !== null);
    const reopened = await fetch(new URL(`/chats/${address}/messages`, asp.origin), {
      method: "POST",
      headers: { authorization: `Bearer ${asp.operatorToken}`, "content-type": "application/json" },
      body: JSON.stringify({ content: "open provider", metadata: { card_result: { id: `open-${card}`, action: "open" } } }),
    });
    assert.equal(reopened.ok, true);
    await until("provider card was not opened", () => cards(magi!.bus).length >= 2);
    assert.equal(magi.bus.settings.get("provider.card.rejections"), "0");
    const before = cards(magi.bus).length;
    await magi.stop();
    magi = new Magi(handle, { workspace, asp: { base: asp.origin, token }, client });
    await magi.start();
    await until("provider card was not shown after a restart", () => cards(magi!.bus).length > before);
    await sleep(400);
    const texts = magi.bus.chats.list().flatMap((candidate) => magi!.bus.messages.list(candidate.id, 50).map((message) => message.content));
    assert.equal(texts.some((text) => text.includes("will not be shown again")), false);
    assert.equal(magi.bus.settings.get("provider.card.rejections"), "0");
  } finally {
    await magi?.stop();
    await asp.stop();
    rmSync(root, { recursive: true, force: true });
  }
});

test("the next start records a replace the shell could not restore", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-replace-marker-"));
  const handle = "@eva-replace-marker-188d.magi";
  const markerDir = path.join(magiHome(), "eva-replace-marker-188d");
  const marker = path.join(markerDir, "replace-failed.json");
  const workspace = path.join(root, "workspace");
  const client = { async complete() { return { role: "assistant" as const, content: "NO_REPLY" }; } };
  let magi: Magi | null = null;
  try {
    const first = new Magi(handle, { workspace, client });
    first.bus.secrets.set("provider.api_key", "present");
    const chat = first.bus.chats.forChannel("cli", "operator");
    messageDelivery.setHomeChat(first.bus, chat.id);
    const id = first.bus.board("ReplaceSelfJob").publish({ checkout: path.join(root, "absent"), swap: true }, "test");
    assert.ok(first.bus.board("ReplaceSelfJob").claimId("replace", id));
    await first.stop();
    mkdirSync(markerDir, { recursive: true });
    writeFileSync(marker, JSON.stringify({ job: id, error: "old process did not start" }));
    magi = new Magi(handle, { workspace, client });
    await magi.start();
    await until("replace marker was not applied", () => magi!.bus.board("ReplaceSelfJob").result(id) !== null);
    const result = magi.bus.board("ReplaceSelfJob").result(id);
    assert.equal(result?.status, "failed");
    assert.equal(result?.error, "old process did not start");
    assert.equal(existsSync(marker), false);
    const notes = magi.bus.messages.list(chat.id).map((message) => message.content);
    assert.ok(notes.some((text) => text.startsWith("[replace] old process did not start")));
  } finally {
    await magi?.stop();
    rmSync(markerDir, { recursive: true, force: true });
    rmSync(root, { recursive: true, force: true });
  }
});

test("a replace marker without a job fails the open replace", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-replace-marker-null-"));
  const handle = "@eva-replace-marker-null-188d.magi";
  const markerDir = path.join(magiHome(), "eva-replace-marker-null-188d");
  const marker = path.join(markerDir, "replace-failed.json");
  const workspace = path.join(root, "workspace");
  const client = { async complete() { return { role: "assistant" as const, content: "NO_REPLY" }; } };
  let magi: Magi | null = null;
  try {
    const first = new Magi(handle, { workspace, client });
    first.bus.secrets.set("provider.api_key", "present");
    const chat = first.bus.chats.forChannel("cli", "operator");
    messageDelivery.setHomeChat(first.bus, chat.id);
    const id = first.bus.board("ReplaceSelfJob").publish({ checkout: path.join(root, "absent"), swap: true }, "test");
    assert.ok(first.bus.board("ReplaceSelfJob").claimId("replace", id));
    await first.stop();
    mkdirSync(markerDir, { recursive: true });
    writeFileSync(marker, JSON.stringify({ job: null, error: "old process did not start" }));
    magi = new Magi(handle, { workspace, client });
    await magi.start();
    await until("replace marker was not applied", () => magi!.bus.board("ReplaceSelfJob").result(id) !== null);
    const result = magi.bus.board("ReplaceSelfJob").result(id);
    assert.equal(result?.status, "failed");
    assert.equal(result?.error, "old process did not start");
    assert.equal(existsSync(marker), false);
    const notes = magi.bus.messages.list(chat.id).map((message) => message.content);
    assert.ok(notes.some((text) => text.startsWith("[replace] old process did not start")));
  } finally {
    await magi?.stop();
    rmSync(markerDir, { recursive: true, force: true });
    rmSync(root, { recursive: true, force: true });
  }
});

test("a corrupt replace marker is reported and removed", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-replace-marker-bad-"));
  const handle = "@eva-replace-marker-bad-188d.magi";
  const markerDir = path.join(magiHome(), "eva-replace-marker-bad-188d");
  const marker = path.join(markerDir, "replace-failed.json");
  const workspace = path.join(root, "workspace");
  const client = { async complete() { return { role: "assistant" as const, content: "NO_REPLY" }; } };
  let magi: Magi | null = null;
  try {
    const first = new Magi(handle, { workspace, client });
    first.bus.secrets.set("provider.api_key", "present");
    const chat = first.bus.chats.forChannel("cli", "operator");
    messageDelivery.setHomeChat(first.bus, chat.id);
    await first.stop();
    mkdirSync(markerDir, { recursive: true });
    writeFileSync(marker, "{");
    magi = new Magi(handle, { workspace, client });
    await magi.start();
    await until("corrupt replace marker was kept", () => !existsSync(marker));
    const notes = magi.bus.messages.list(chat.id).map((message) => message.content);
    assert.ok(notes.some((text) => text.startsWith("[replace] ")));
  } finally {
    await magi?.stop();
    rmSync(markerDir, { recursive: true, force: true });
    rmSync(root, { recursive: true, force: true });
  }
});

test("a replace marker for a missing job is reported and removed", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-replace-marker-missing-"));
  const handle = "@eva-replace-marker-missing-188d.magi";
  const markerDir = path.join(magiHome(), "eva-replace-marker-missing-188d");
  const marker = path.join(markerDir, "replace-failed.json");
  const workspace = path.join(root, "workspace");
  const client = { async complete() { return { role: "assistant" as const, content: "NO_REPLY" }; } };
  let magi: Magi | null = null;
  try {
    const first = new Magi(handle, { workspace, client });
    first.bus.secrets.set("provider.api_key", "present");
    const chat = first.bus.chats.forChannel("cli", "operator");
    messageDelivery.setHomeChat(first.bus, chat.id);
    await first.stop();
    mkdirSync(markerDir, { recursive: true });
    writeFileSync(marker, JSON.stringify({ job: 999999, error: "old process did not start" }));
    magi = new Magi(handle, { workspace, client });
    await magi.start();
    await until("missing replace job marker was kept", () => !existsSync(marker));
    const notes = magi.bus.messages.list(chat.id).map((message) => message.content);
    assert.ok(notes.some((text) => text.startsWith("[replace] old process did not start")));
    assert.equal(magi.bus.board("ReplaceSelfJob").result(999999), null);
  } finally {
    await magi?.stop();
    rmSync(markerDir, { recursive: true, force: true });
    rmSync(root, { recursive: true, force: true });
  }
});

test("a replace whose old process also fails leaves a marker and tells the shell", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-shell-marker-"));
  const handle = "@eva-shell-marker-188d.magi";
  const markerDir = path.join(magiHome(), "eva-shell-marker-188d");
  const marker = path.join(markerDir, "replace-failed.json");
  const log = path.join(root, "shell-events.log");
  const shellCwd = path.join(root, "apps", "shell");
  const evaCwd = path.join(root, "apps", "eva");
  mkdirSync(path.join(shellCwd, "dist"), { recursive: true });
  mkdirSync(path.join(evaCwd, "dist"), { recursive: true });
  writeFileSync(path.join(shellCwd, "dist", "shell.js"), `const { createInterface } = require("node:readline");
const { appendFileSync } = require("node:fs");
process.send({ type: "ready" });
createInterface({ input: process.stdin }).on("line", (line) => appendFileSync(process.env.LOG, line + "\\n"));
setInterval(() => {}, 1000);
`);
  writeFileSync(path.join(evaCwd, "dist", "eva.js"), `if (process.env.MAGI_REPLACE_JOB || process.env.MAGI_REPLACE_ERROR) process.exit(1);
process.send({ type: "ready" });
process.send({ type: "runtime.replace", job: 41 });
setInterval(() => {}, 1000);
`);
  const supervisor = createSupervisor({ node: process.execPath, env: process.env, readyTimeoutMs: 5_000, stopTimeoutMs: 5_000 });
  try {
    const shell = await supervisor.start(
      { entry: path.join(shellCwd, "dist", "shell.js"), cwd: shellCwd, env: { LOG: log } },
      { host: {} },
    );
    await shell.whenReady();
    const eva = await supervisor.start({ entry: path.join(evaCwd, "dist", "eva.js"), cwd: evaCwd, args: [handle] });
    await eva.whenReady();
    await until("replace marker was not written", () => {
      if (!existsSync(marker) || !existsSync(log)) return false;
      return readFileSync(log, "utf8").includes("\"event\":\"process.replace\"");
    }, 15_000);
    const body = JSON.parse(readFileSync(marker, "utf8")) as { job?: number; error?: string };
    assert.equal(body.job, 41);
    assert.match(body.error ?? "", /exited before ready/);
    assert.equal(existsSync(path.join(markerDir, "memories")), false);
    assert.equal(existsSync(path.join(markerDir, "logs")), false);
  } finally {
    await supervisor.close();
    rmSync(markerDir, { recursive: true, force: true });
    rmSync(root, { recursive: true, force: true });
  }
});

test("a broken eva build is restored and the operator is told", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-eva-rollback-"));
  const checkout = writeBrokenApp(root, "eva", "build");
  const magi = new Magi("@eva-002.magi", { workspace: path.join(root, "workspace"), client: { async complete() { return { role: "assistant" as const, content: "NO_REPLY" }; } } });
  magi.bus.secrets.set("provider.api_key", "present");
  try {
    await magi.start();
    await expectRollback(magi.bus, checkout, "eva");
  } finally {
    await magi.stop();
    rmSync(root, { recursive: true, force: true });
  }
});

test("a broken asp build is restored and the operator is told", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-asp-rollback-"));
  const checkout = writeBrokenApp(root, "asp", "build");
  const asp = new Asp({
    databasePath: path.join(root, "asp.sqlite"),
    workspace: path.join(root, "workspace"),
    host: "127.0.0.1",
    port: 0,
    client: { async complete() { return { role: "assistant" as const, content: "NO_REPLY" }; } },
  });
  asp.bus.secrets.set("provider.api_key", "present");
  try {
    await asp.start();
    await expectRollback(asp.bus, checkout, "asp");
  } finally {
    await asp.stop();
    rmSync(root, { recursive: true, force: true });
  }
});

test("a broken shell build is restored and the operator is told", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-shell-rollback-"));
  const checkout = writeBrokenApp(root, "shell", "build:process");
  const workspace = path.join(root, "workspace");
  const bus = new Bus("@eva-000.magi", workspace);
  bus.secrets.set("provider.api_key", "present");
  const manager = new ManagerWorker(bus, [], { build: { app: "shell", script: "build:process" } });
  try {
    await manager.start();
    await expectRollback(bus, checkout, "shell");
  } finally {
    await manager.stop();
    bus.close();
    rmSync(root, { recursive: true, force: true });
  }
});

test("provider sync stores the key and keeps it out of the message text", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-sync-"));
  const asp = new Asp({
    databasePath: path.join(root, "asp.sqlite"),
    workspace: path.join(root, "casper"),
    host: "127.0.0.1",
    port: 0,
    client: {
      async complete() { return { role: "assistant" as const, content: "NO_REPLY" }; },
      async verify() {},
      configure() {},
    },
  });
  let magi: Magi | null = null;
  try {
    await asp.start();
    const known = new Set(["@eva-000.magi", "@eva-001.magi", "@eva-002.magi"]);
    const bots: Array<{ handle: string; token: string }> = [];
    while (bots.length < 2 || !bots.some((bot) => !known.has(bot.handle))) {
      const created = await fetch(new URL("/chats", asp.origin), {
        method: "POST",
        headers: { authorization: `Bearer ${asp.operatorToken}`, "content-type": "application/json" },
        body: JSON.stringify({ kind: "bot" }),
      });
      const body = await created.json() as { magi?: { handle?: string; token?: string } };
      bots.push({ handle: body.magi?.handle ?? "", token: body.magi?.token ?? "" });
      if (bots.length > 6) break;
    }
    const strangerBot = bots.find((bot) => !known.has(bot.handle));
    const receiverBot = bots.find((bot) => bot !== strangerBot);
    assert.ok(strangerBot && receiverBot);
    const handle = receiverBot.handle;
    const token = receiverBot.token;
    const strangerHandle = strangerBot.handle;
    const strangerToken = strangerBot.token;
    magi = new Magi(handle, {
      workspace: path.join(root, "receiver"),
      asp: { base: asp.origin, token },
      client: {
        async complete() { return { role: "assistant" as const, content: "NO_REPLY" }; },
        async verify() {},
        configure() {},
      },
    });
    await magi.start();
    if (!known.has(handle)) asp.bus.contacts.create({ name: handle, role: "magi", asp_handle: handle });
    const strangerContact = asp.bus.contacts.list().find((contact) => contact.asp_handle === strangerHandle);
    if (strangerContact) asp.bus.contacts.update(strangerContact.id, { role: "stranger" });
    const posted = await fetch(new URL("/profile", asp.origin), {
      method: "POST",
      headers: { authorization: `Bearer ${strangerToken}`, "content-type": "application/json" },
      body: JSON.stringify({ public_key: "not-a-key" }),
    });
    assert.equal(posted.ok, true);
    asp.bus.settings.set("provider.name", "openai");
    asp.bus.settings.set("provider.model", "gpt-test");
    asp.bus.secrets.set("provider.api_key", "sync-secret-value");
    const tool = asp.bus.tools.get("sync_provider");
    assert.ok(tool);
    const queued = JSON.parse(await tool.run({})) as { id: number; status: string };
    assert.equal(typeof queued.id, "number");
    assert.equal(queued.status, "pending");
    await until("receiver did not store the provider key", () => magi?.bus.secrets.get("provider.api_key") === "sync-secret-value");
    await until("sync did not finish", () => {
      const result = asp.bus.board("AspCommandJob").result(queued.id);
      if (result?.status === "failed") throw new Error(result.error ?? "sync failed");
      return result?.status === "completed";
    });
    const leaked = [asp.bus, magi.bus].flatMap((item) =>
      item.chats.list().flatMap((chat) => item.messages.list(chat.id, 100).filter((message) => message.content.includes("sync-secret-value"))));
    assert.equal(leaked.length, 0);
    const notice = asp.bus.chats.list("asp").flatMap((chat) => asp.bus.messages.list(chat.id, 50).map((message) => message.content));
    assert.ok(notice.some((text) => text.startsWith("provider sync:") && text.includes("key updated")));
  } finally {
    await magi?.stop();
    await asp.stop();
    rmSync(root, { recursive: true, force: true });
  }
});

import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

import { Bus, chat as receiveChat, encryptFor, messageDelivery, replaceFailedPath, type CardSpec, type ChatInput } from "@magi/bus";
import { createOwnerApi } from "@magi/launcher/composition/owner.js";
import { Magi } from "@magi/launcher/composition/agent.js";
import { createSupervisor } from "../process.mjs";

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(label: string, ready: () => boolean | Promise<boolean>, timeoutMs = 20_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) { if (await ready()) return; await sleep(30); }
  throw new Error(label);
}
function cards(bus: Bus): string[] {
  return bus.chats.list().flatMap(chat => bus.messages.list(chat.id, 100).map(message => message.content)).filter(content => content === "card: Provider");
}
function ownerCards(magi: Magi): CardSpec[] {
  return createOwnerApi(magi.bus)["owner.events"]().flatMap(event => event.metadata?.card ? [event.metadata.card as CardSpec] : []);
}
async function ownerChat(magi: Magi, input: ChatInput): Promise<void> {
  const receipt = createOwnerApi(magi.bus)["owner.chat"](input);
  await until("owner control did not settle", () => magi.bus.board(receipt.type).result(receipt.id) !== null);
  assert.equal(magi.bus.board(receipt.type).result(receipt.id)?.status, "completed");
}
function localMagi(handle: string, workspace: string, client: NonNullable<ConstructorParameters<typeof Magi>[1]>["client"]): Magi {
  const magi = new Magi(handle, { workspace, client, owner: true, peer: { port: 0 } });
  createOwnerApi(magi.bus);
  return magi;
}
function isolateHome(root: string): () => void {
  const previous = process.env.MAGI_HOME;
  process.env.MAGI_HOME = path.join(root, "home");
  return () => { if (previous === undefined) delete process.env.MAGI_HOME; else process.env.MAGI_HOME = previous; };
}

test("a fresh EVA owner channel shows a card and reopens it after cancellation without a model", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-card-"));
  let calls = 0;
  const client = { async complete() { calls++; return { role: "assistant" as const, content: "NO_REPLY" }; }, async verify() { throw new Error("rejected"); } };
  let magi = localMagi("@card-test.magi", path.join(root, "receiver"), client);
  try {
    await magi.start();
    await until("provider card was not delivered", () => ownerCards(magi).length === 1);
    const card = ownerCards(magi)[0];
    assert.ok(card.id && card.public_key);
    assert.equal(calls, 0);
    await ownerChat(magi, { content: "card rejected", metadata: { card_reject: { id: card.id } } });
    await until("cancellation was not acknowledged", () => magi.bus.settings.get(`card.receipt.${card.id}`) !== null);
    await ownerChat(magi, { content: "open provider", metadata: { card_result: { id: `open-${card.id}`, action: "open" } } });
    await until("provider card was not reopened", () => ownerCards(magi).some(next => next.id !== card.id));
    const again = ownerCards(magi).find(next => next.id !== card.id)!;
    await ownerChat(magi, { content: "card rejected", metadata: { card_reject: { id: again.id } } });
    await until("second cancellation did not stop offers", () => magi.bus.settings.get("provider.card.stopped") === "1");
    const before = ownerCards(magi).length;
    await magi.stop(); magi = localMagi("@card-test.magi", path.join(root, "receiver"), client);
    await magi.start(); await sleep(400);
    assert.equal(ownerCards(magi).length, before);
    assert.equal(magi.bus.settings.get("provider.card.stopped"), "1");
    assert.equal(calls, 0); assert.equal(magi.bus.secrets.get("provider.api_key"), null);
  } finally { await magi.stop(); rmSync(root, { recursive: true, force: true }); }
});

test("two restarts do not count as rejecting the provider card or duplicate the owner room", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-card-restart-")); let calls = 0;
  const client = { async complete() { calls++; return { role: "assistant" as const, content: "NO_REPLY" }; } };
  let magi = localMagi("@card-restart.magi", path.join(root, "receiver"), client);
  try {
    await magi.start(); await until("initial card not shown", () => cards(magi.bus).length >= 1);
    const chatId = magi.bus.chats.list("local-owner")[0].id;
    for (let restart = 0; restart < 2; restart++) {
      const before = cards(magi.bus).length;
      await magi.stop(); magi = localMagi("@card-restart.magi", path.join(root, "receiver"), client);
      await magi.start(); await until("card not shown after restart", () => cards(magi.bus).length > before);
    }
    const texts = magi.bus.messages.list(chatId, 100).map(message => message.content);
    assert.equal(texts.some(text => text.includes("rejected") || text.includes("will not be shown again")), false);
    assert.equal(magi.bus.settings.get("provider.card.rejections") ?? "0", "0");
    assert.equal(calls, 0); assert.equal(magi.bus.chats.list("local-owner").length, 1);
    assert.equal(magi.bus.chats.list("local-owner")[0].id, chatId);
  } finally { await magi.stop(); rmSync(root, { recursive: true, force: true }); }
});

test("an explicitly reopened owner card survives restart after cancellation", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-card-reopen-"));
  const client = { async complete() { return { role: "assistant" as const, content: "NO_REPLY" }; } };
  let magi = localMagi("@card-reopen.magi", path.join(root, "receiver"), client);
  try {
    await magi.start(); await until("initial card not delivered", () => ownerCards(magi).length > 0);
    const card = ownerCards(magi)[0];
    await ownerChat(magi, { content: "card rejected", metadata: { card_reject: { id: card.id } } });
    await ownerChat(magi, { content: "open provider", metadata: { card_result: { id: `reopen-${card.id}`, action: "open" } } });
    await until("reopened card not delivered", () => ownerCards(magi).some(next => next.id !== card.id));
    assert.equal(magi.bus.settings.get("provider.card.rejections"), null);
    const before = cards(magi.bus).length;
    await magi.stop(); magi = localMagi("@card-reopen.magi", path.join(root, "receiver"), client);
    await magi.start(); await until("reopened card did not survive restart", () => cards(magi.bus).length > before);
    assert.equal(magi.bus.settings.get("provider.card.rejections"), null);
  } finally { await magi.stop(); rmSync(root, { recursive: true, force: true }); }
});

test("the next start records a replace the shell could not restore", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-replace-marker-"));
  const handle = "@eva-replace-marker-188d.magi";
  const restoreHome = isolateHome(root);
  const marker = replaceFailedPath(handle);
  const markerDir = path.dirname(marker);
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
    restoreHome();
  }
});

test("a replace marker without a job fails the open replace", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-replace-marker-null-"));
  const handle = "@eva-replace-marker-null-188d.magi";
  const restoreHome = isolateHome(root);
  const marker = replaceFailedPath(handle);
  const markerDir = path.dirname(marker);
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
    restoreHome();
  }
});

test("a corrupt replace marker is reported and removed", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-replace-marker-bad-"));
  const handle = "@eva-replace-marker-bad-188d.magi";
  const restoreHome = isolateHome(root);
  const marker = replaceFailedPath(handle);
  const markerDir = path.dirname(marker);
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
    restoreHome();
  }
});

test("a replace marker for a missing job is reported and removed", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-replace-marker-missing-"));
  const handle = "@eva-replace-marker-missing-188d.magi";
  const restoreHome = isolateHome(root);
  const marker = replaceFailedPath(handle);
  const markerDir = path.dirname(marker);
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
    restoreHome();
  }
});

test("a replace whose old process also fails leaves a marker and tells the shell", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-shell-marker-"));
  const handle = "@eva-shell-marker-188d.magi";
  const restoreHome = isolateHome(root);
  const marker = replaceFailedPath(handle);
  const markerDir = path.dirname(marker);
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
    restoreHome();
  }
});


test("encrypted local-owner provider submission stores the key without exposing it in history or peer controls", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-owner-provider-")); let verified = 0;
  const secret = "synthetic-owner-provider-secret";
  const magi = localMagi("@provider-owner.magi", path.join(root, "receiver"), {
    async complete() { throw new Error("provider setup must not invoke a model"); },
    async verify(settings) { verified++; assert.equal(settings.api_key, secret); }, configure() {},
  });
  try {
    await magi.start(); await until("provider card not delivered", () => ownerCards(magi).length > 0);
    const card = ownerCards(magi)[0]; assert.ok(card.public_key);
    const ciphertext = encryptFor(card.public_key, secret);
    const peer = magi.bus.contacts.forPeer({ principal: "a".repeat(64), endpoint: "https://peer.example", name: "system" });
    const room = magi.bus.chats.forPeer(peer.peer_id!);
    const payload = { content: secret, metadata: { card_result: { id: card.id, attempt: "owner-submit", fields: {
      provider: "custom", model: "synthetic-model", base_url: "https://provider.example/v1", "provider.api_key": { ciphertext },
    } } } };
    assert.throws(() => receiveChat(magi.bus, room.id, peer.id, payload), /only the owner/);
    assert.equal(verified, 0);
    await ownerChat(magi, payload);
    assert.equal(magi.bus.secrets.get("provider.api_key"), secret); assert.equal(verified, 1);
    // A repeated owner submission must not verify or apply the same attempt twice.
    await ownerChat(magi, payload); assert.equal(verified, 1);
    const exposed = JSON.stringify({ events: createOwnerApi(magi.bus)["owner.events"](), history: magi.bus.chats.list().flatMap(chat => magi.bus.messages.list(chat.id, 100)) });
    assert.equal(exposed.includes(secret), false); assert.equal(exposed.includes(ciphertext), false);
    assert.equal(magi.bus.settings.get("provider.model"), "synthetic-model");
  } finally { await magi.stop(); rmSync(root, { recursive: true, force: true }); }
});

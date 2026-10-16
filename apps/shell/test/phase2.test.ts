import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

import { MAGI_CONTACT_ID } from "@magi/bus";
import { Asp } from "../../asp/asp.ts";
import { Magi } from "../../eva/eva.ts";

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const SECRET = "sync-secret-value";

function quietClient() {
  return {
    async complete() { return { role: "assistant" as const, content: "NO_REPLY" }; },
    async verify() {},
    configure() {},
  };
}

async function until(label: string, ready: () => boolean | Promise<boolean>): Promise<void> {
  const deadline = Date.now() + 15_000;
  while (Date.now() < deadline) {
    if (await ready()) return;
    await sleep(30);
  }
  throw new Error(label);
}

function writeCheckout(root: string, buildBody: string, entry = "export const version = 1;\n"): string {
  const checkout = path.join(root, "checkout");
  mkdirSync(path.join(checkout, "apps", "eva", "dist"), { recursive: true });
  writeFileSync(path.join(checkout, "package.json"), JSON.stringify({
    type: "module",
    scripts: { build: "node build.js" },
  }));
  writeFileSync(path.join(checkout, "build.js"), buildBody);
  writeFileSync(path.join(checkout, "apps", "eva", "dist", "eva.js"), entry);
  return checkout;
}

test("a MAGI with no provider key shows the provider card and does not call the model", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-card-"));
  let calls = 0;
  const magi = new Magi("@eva-002.magi", {
    workspace: root,
    client: { async complete() { calls += 1; return { role: "assistant", content: "NO_REPLY" }; } },
  });
  const chat = magi.bus.chats.forChannel("asp", "with-operator");
  magi.bus.chatMembers.add(chat.id, MAGI_CONTACT_ID);
  try {
    await magi.start();
    await until("provider card was not shown", () =>
      magi.bus.messages.list(chat.id).some((message) => message.content === "card: Provider"));
    assert.equal(calls, 0);
    assert.equal(magi.bus.secrets.get("provider.api_key"), null);
  } finally {
    await magi.stop();
    rmSync(root, { recursive: true, force: true });
  }
});

test("a broken build is restored and the replace job fails", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-rollback-"));
  const checkout = writeCheckout(root, `
    import { mkdirSync, writeFileSync } from "node:fs";
    mkdirSync("apps/eva/dist", { recursive: true });
    writeFileSync("apps/eva/dist/eva.js", "function (");
  `);
  const magi = new Magi("@eva-002.magi", { workspace: root, client: quietClient() });
  try {
    await magi.start();
    const id = magi.bus.board("ReplaceSelfJob").publish({ checkout, swap: false }, "test");
    await until("replace did not finish", () => magi.bus.board("ReplaceSelfJob").result(id) !== null);
    const result = magi.bus.board("ReplaceSelfJob").result(id);
    assert.equal(result?.status, "failed");
    assert.equal(typeof result?.error, "string");
    assert.ok((result?.error ?? "").length > 0);
    assert.equal(readFileSync(path.join(checkout, "apps", "eva", "dist", "eva.js"), "utf8"), "export const version = 1;\n");
  } finally {
    await magi.stop();
    rmSync(root, { recursive: true, force: true });
  }
});

test("a good build replaces the previous entry", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-replace-"));
  const checkout = writeCheckout(root, `
    import { mkdirSync, writeFileSync } from "node:fs";
    mkdirSync("apps/eva/dist", { recursive: true });
    writeFileSync("apps/eva/dist/eva.js", "export const version = 2;\\n");
  `);
  const magi = new Magi("@eva-002.magi", { workspace: root, client: quietClient() });
  try {
    await magi.start();
    const id = magi.bus.board("ReplaceSelfJob").publish({ checkout, swap: false }, "test");
    await until("replace did not finish", () => magi.bus.board("ReplaceSelfJob").result(id) !== null);
    assert.equal(magi.bus.board("ReplaceSelfJob").result(id)?.status, "completed");
    assert.equal(readFileSync(path.join(checkout, "apps", "eva", "dist", "eva.js"), "utf8"), "export const version = 2;\n");
  } finally {
    await magi.stop();
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
    client: quietClient(),
  });
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
      client: quietClient(),
    });
    await magi.start();
    asp.bus.settings.set("provider.name", "openai");
    asp.bus.settings.set("provider.model", "gpt-test");
    asp.bus.secrets.set("provider.api_key", SECRET);
    const tool = asp.bus.tools.get("sync_provider");
    assert.ok(tool);
    await tool.run({});
    await until("receiver did not store the provider key", () => magi?.bus.secrets.get("provider.api_key") === SECRET);
    const leaked = [asp.bus, magi.bus].flatMap((bus) =>
      bus.chats.list().flatMap((chat) => bus.messages.list(chat.id, 100).filter((message) => message.content.includes(SECRET))));
    assert.equal(leaked.length, 0);
    const notice = asp.bus.chats.list("asp").flatMap((chat) => asp.bus.messages.list(chat.id, 50).map((message) => message.content));
    assert.ok(notice.some((text) => text.startsWith("provider sync:") && text.includes("key updated")));
  } finally {
    await magi?.stop();
    await asp.stop();
    rmSync(root, { recursive: true, force: true });
  }
});

test("a ready replacement takes the place of the old process", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-swap-"));
  const readyFile = path.join(root, "ready");
  const entry = `import { writeFileSync } from "node:fs";
writeFileSync(process.env.READY_FILE, String(process.pid));
process.send?.({ type: "ready" });
setInterval(() => {}, 1000);
`;
  const checkout = writeCheckout(root, `
    import { mkdirSync, writeFileSync } from "node:fs";
    mkdirSync("apps/eva/dist", { recursive: true });
    writeFileSync("apps/eva/dist/eva.js", ${JSON.stringify(entry)});
  `);
  const repo = path.resolve(import.meta.dirname, "../../..");
  const child = spawn(process.execPath, ["--input-type=module", "-e", `
    import { Magi } from ${JSON.stringify(path.join(repo, "apps/eva/dist/eva.js"))};
    const magi = new Magi("@swap.magi", {
      workspace: ${JSON.stringify(path.join(root, "workspace"))},
      client: { async complete() { return { role: "assistant", content: "NO_REPLY" }; } },
    });
    await magi.start();
    const id = magi.bus.board("ReplaceSelfJob").publish({ checkout: ${JSON.stringify(checkout)}, swap: true }, "test");
    const deadline = Date.now() + 20000;
    while (Date.now() < deadline) {
      const result = magi.bus.board("ReplaceSelfJob").result(id);
      if (result?.status === "failed") { console.error(result.error); process.exit(2); }
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    process.exit(3);
  `], { cwd: repo, env: { ...process.env, READY_FILE: readyFile } });
  let code = -1;
  let output = "";
  child.stdout?.on("data", (chunk) => { output += String(chunk); });
  child.stderr?.on("data", (chunk) => { output += String(chunk); });
  try {
    code = await new Promise((resolve) => child.once("exit", resolve));
    assert.equal(code, 0, output);
    const pid = Number(readFileSync(readyFile, "utf8"));
    process.kill(pid, 0);
    process.kill(pid, "SIGTERM");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

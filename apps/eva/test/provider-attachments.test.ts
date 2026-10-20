import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, rmSync, symlinkSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { attachmentText, storeAttachments } from "@magi/bus/attachments.js";
import type { LLMMessage } from "@magi/bus";
import { PiAiClient, type ProviderSettings } from "@magi/providers/client.js";

const png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2N8sAAAAASUVORK5CYII=";
const imageInput = { name: "pixel.png", mime_type: "image/png", data_base64: png };
async function withWorkspace(run: (path: string) => Promise<void>) {
  const path = mkdtempSync(join(tmpdir(), "magi-native-images-"));
  try { await run(path); } finally { rmSync(path, { recursive: true, force: true }); }
}

async function request(settings: ProviderSettings, messages: LLMMessage[], workspace?: string): Promise<Record<string, unknown>> {
  let body: Record<string, unknown> | undefined;
  const client = new PiAiClient({ ...settings, api_key: "test" }, (async (_url, init) => {
    body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    // We inspect the real adapter's wire request, not a mocked context builder.
    return Response.json({ error: { type: "invalid_request_error", message: "test response" } }, { status: 400 });
  }) as typeof fetch, workspace);
  await assert.rejects(client.complete({ messages, tools: [] }));
  assert.ok(body, "the provider adapter dispatched a formatted request");
  return body;
}

test("image-capable Anthropic and OpenAI requests include native bytes and stable path text", async () => withWorkspace(async path => {
  const attachments = storeAttachments(path, "42", [imageInput]);
  const message: LLMMessage = { role: "user", content: `What is pictured?\n${attachmentText(attachments)}`, attachments, attachment_scope: "42" };
  for (const settings of [{ provider: "anthropic", model: "claude-haiku-4-5" }, { provider: "openai", model: "gpt-4.1" }]) {
    const body = await request(settings, [message], path);
    const serialized = JSON.stringify(body);
    assert.ok(serialized.includes(png), `${settings.provider} received native image bytes`);
    assert.ok(serialized.includes("image/png"));
    assert.ok(serialized.includes(attachments[0].path));
  }
}));

test("text-only/custom providers keep attachment paths but never receive binary files", async () => withWorkspace(async path => {
  const attachments = storeAttachments(path, "42", [
    imageInput,
    { name: "clip.mp4", mime_type: "video/mp4", data_base64: Buffer.from("private video bytes").toString("base64") },
    { name: "voice.wav", mime_type: "audio/wav", data_base64: Buffer.from("private audio bytes").toString("base64") },
    { name: "report.pdf", mime_type: "application/pdf", data_base64: Buffer.from("private document bytes").toString("base64") },
  ]);
  const message: LLMMessage = { role: "user", content: attachmentText(attachments), attachments, attachment_scope: "42" };
  const custom = await request({ provider: "custom", model: "text-endpoint", base_url: "http://localhost:1234/v1" }, [message], path);
  const serialized = JSON.stringify(custom);
  for (const attachment of attachments) assert.ok(serialized.includes(attachment.path));
  assert.ok(!serialized.includes(png));
  assert.ok(!serialized.includes("private video bytes"));
  const vision = JSON.stringify(await request({ provider: "anthropic", model: "claude-haiku-4-5" }, [message], path));
  assert.ok(vision.includes(png));
  for (const text of ["private video bytes", "private audio bytes", "private document bytes"]) {
    assert.ok(!vision.includes(Buffer.from(text).toString("base64")));
  }
}));

test("missing workspace, wrong conversation, forged path and false MIME claims cannot attach native bytes", async () => withWorkspace(async path => {
  const attachments = storeAttachments(path, "42", [imageInput]);
  const settings = { provider: "anthropic", model: "claude-haiku-4-5" };
  const base: LLMMessage = { role: "user", content: attachmentText(attachments), attachments, attachment_scope: "42" };
  for (const [message, root] of [
    [base, undefined],
    [{ ...base, attachment_scope: "43" }, path],
    [{ ...base, attachments: [{ ...attachments[0], path: "/etc/passwd" }] }, path],
    [{ ...base, attachments: [{ ...attachments[0], mime_type: "image/jpeg" }] }, path],
    [{ ...base, role: "system" }, path],
  ] as Array<[LLMMessage, string | undefined]>) {
    const serialized = JSON.stringify(await request(settings, [message, { role: "user", content: "Continue." }], root));
    assert.ok(!serialized.includes(png));
    assert.ok(serialized.includes(attachments[0].path));
  }
}));

test("a symlinked or deleted image degrades to path-only text without reading its replacement", async () => withWorkspace(async path => {
  const attachments = storeAttachments(path, "42", [imageInput]);
  const message: LLMMessage = { role: "user", content: attachmentText(attachments), attachments, attachment_scope: "42" };
  const target = join(path, attachments[0].path);
  unlinkSync(target);
  const settings = { provider: "anthropic", model: "claude-haiku-4-5" };
  assert.ok(!JSON.stringify(await request(settings, [message], path)).includes(png));
  const outside = join(path, "private.png");
  writeFileSync(outside, Buffer.from(png, "base64"));
  symlinkSync(outside, target);
  assert.ok(!JSON.stringify(await request(settings, [message], path)).includes(png));
}));

test("native image request limits prioritize the latest messages while retaining older attachment paths", async () => withWorkspace(async path => {
  const messages: LLMMessage[] = [];
  for (let index = 0; index < 9; index++) {
    const attachments = storeAttachments(path, "42", [{ ...imageInput, name: `image-${index}.png` }]);
    messages.push({ role: "user", content: attachmentText(attachments), attachments, attachment_scope: "42" });
  }
  const body = await request({ provider: "anthropic", model: "claude-haiku-4-5" }, messages, path);
  const sent = body.messages as Array<{ content: string | Array<{ type: string }> }>;
  assert.equal(sent.length, 9);
  assert.equal(typeof sent[0].content, "string");
  assert.ok(Array.isArray(sent.at(-1)!.content) && (sent.at(-1)!.content as Array<{ type: string }>).some(part => part.type === "image"));
  assert.equal(sent.flatMap(message => Array.isArray(message.content) ? message.content : []).filter(part => part.type === "image").length, 8);
}));

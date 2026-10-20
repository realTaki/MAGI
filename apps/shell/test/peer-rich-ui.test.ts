import assert from "node:assert/strict";
import test from "node:test";
import { peerMessages } from "../web/src/peer-messages.ts";
import { reactPeerMessage, readPeerAttachment, sendPeerMessage } from "../web/src/peer.ts";
import { encodeAttachment, formatFileSize, groupedReactions, MAX_UPLOAD_BYTES, messageSummary, replyMessage, validateFiles } from "../web/src/rich-messages.ts";

const contact = { id: "shell", handle: "@shell.magi", name: "Shell", nickname: "", url: "http://localhost:1234", local: true };
const attachment = { id: "asset-1", name: "diagram.png", mime_type: "image/png", size: 42, path: "/workspace/attachments/diagram.png" };

test("stable reply IDs, attachment references, and reaction ownership survive event presentation", () => {
  const events = [
    { id: "message-1", content: "@peer.magi please look", role: "user" as const, created_at: "" },
    { id: 52, content: "", role: "assistant" as const, created_at: "", reply_to: "message-1", attachments: [attachment], reactions: [{ emoji: "👍", actor: "magi:self", mine: true }] },
  ];
  const messages = peerMessages(contact, events);
  assert.equal(messages[0].id, "message-1");
  assert.equal(messages[0].text, "@peer.magi please look");
  assert.equal(messages[1].id, 52);
  assert.equal(messages[1].reply_to, "message-1");
  assert.deepEqual(messages[1].attachments, [attachment]);
  assert.equal(messages[1].reactions?.[0].mine, true);
  assert.equal(replyMessage(messages, "message-1"), messages[0]);
  assert.equal(replyMessage([...messages].reverse(), "message-1"), messages[0]);
  assert.equal(replyMessage(messages, "52"), messages[1]);
  assert.equal(replyMessage(messages, "deleted-message"), undefined);
  assert.equal(messageSummary(messages[1]), "diagram.png");
});

test("rich sends, reactions, and attachment reads use only bound local bridge identifiers", async () => {
  const calls: unknown[] = [];
  const previous = Object.getOwnPropertyDescriptor(globalThis, "window");
  Object.defineProperty(globalThis, "window", { configurable: true, value: { magiDesktop: { invokeLocal: async (method: string, payload: unknown) => { calls.push([method, payload]); } } } });
  try {
    const upload = { name: "diagram.png", mime_type: "image/png", data_base64: "AAEC" };
    await sendPeerMessage("conversation:uuid", "@friend.magi", undefined, { reply_to: "stable-id", attachments: [upload] });
    await sendPeerMessage("shell", "", undefined, { attachments: [upload] });
    await sendPeerMessage("shell", "", { card_result: { id: "card" } });
    await reactPeerMessage("conversation:uuid", "stable-id", "👍");
    await reactPeerMessage("shell", 0, "❤️", true);
    await readPeerAttachment("shell", 0, "asset-1");
    assert.deepEqual(calls, [
      ["peer.send", { id: "conversation:uuid", content: "@friend.magi", reply_to: "stable-id", attachments: [upload] }],
      ["peer.send", { id: "shell", content: "", attachments: [upload] }],
      ["peer.send", { id: "shell", content: "", metadata: { card_result: { id: "card" } } }],
      ["peer.react", { id: "conversation:uuid", message_id: "stable-id", emoji: "👍", remove: false }],
      ["peer.react", { id: "shell", message_id: 0, emoji: "❤️", remove: true }],
      ["peer.attachment", { id: "shell", message_id: 0, attachment_id: "asset-1" }],
    ]);
  } finally {
    if (previous) Object.defineProperty(globalThis, "window", previous);
    else Reflect.deleteProperty(globalThis, "window");
  }
});

test("reaction chips count unique actors and use authenticated mine flags rather than a guessed actor name", () => {
  assert.deepEqual(groupedReactions([
    { actor: "@user.magi", emoji: "👍", mine: false },
    { actor: "magi:local-key", emoji: "👍", mine: true },
    { actor: "magi:local-key", emoji: "👍", mine: true },
    { actor: "magi:remote", emoji: "❤️" },
  ]), [
    { emoji: "👍", actors: ["@user.magi", "magi:local-key"], mine: true },
    { emoji: "❤️", actors: ["magi:remote"], mine: false },
  ]);
  assert.equal(groupedReactions([{ actor: "@user.magi", emoji: "👀" }])[0].mine, false);
});

test("upload limits are enforced on the entire draft and binary bytes round-trip unchanged", async () => {
  assert.equal(validateFiles(Array.from({ length: 8 }, () => ({ size: 1 }))), undefined);
  assert.equal(validateFiles(Array.from({ length: 9 }, () => ({ size: 1 }))), "attachmentCount");
  assert.equal(validateFiles([{ size: MAX_UPLOAD_BYTES }]), undefined);
  assert.equal(validateFiles([{ size: MAX_UPLOAD_BYTES }, { size: 1 }]), "attachmentTotal");
  const bytes = Uint8Array.from({ length: 100_001 }, (_, index) => index % 256);
  const upload = await encodeAttachment(new File([bytes], "测试.bin"));
  assert.equal(upload.name, "测试.bin");
  assert.equal(upload.mime_type, "application/octet-stream");
  assert.deepEqual(Buffer.from(upload.data_base64, "base64"), Buffer.from(bytes));
  assert.equal(formatFileSize(23), "23 B");
  assert.equal(formatFileSize(2048), "2.0 KB");
  assert.equal(formatFileSize(1_048_576), "1.0 MB");
});

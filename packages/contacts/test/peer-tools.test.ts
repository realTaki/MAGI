import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Bus } from "@magi/bus";
import { contactTools } from "../tools.js";

test("trusted-LAN agent saves a peer contact without approval policy or owner context", async () => {
  const root = mkdtempSync(join(tmpdir(), "magi-peer-tool-")); const bus = new Bus("@test.magi", root);
  try {
    const contact = bus.contacts.forPeer({ principal: "a".repeat(64), endpoint: "http://192.168.1.2:8080", name: "Other EVA" });
    const tools = contactTools(bus);
    const save = tools.find(tool => tool.name === "accept_peer_contact")!;
    await save.run({ contact_id: contact.id, endpoint: "http://192.168.1.3:8080" });
    assert.equal(bus.contacts.get(contact.id)?.peer_url, "http://192.168.1.3:8080");
    assert.equal(bus.contacts.get(contact.id)?.peer_trust, "accepted");
    assert.ok(!tools.some(tool => tool.name === "set_peer_tool_policy" || tool.name === "set_peer_contact_policy"));
  } finally { bus.close(); rmSync(root, { recursive: true, force: true }); }
});

import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { Bus, MAGI_CONTACT_ID, SHELL_MAGI_HANDLE } from "@magi/bus";
import { Shell } from "@magi/launcher/composition";

const repository = path.resolve(import.meta.dirname, "../../..");
test("cold Shell starts only its built-in EVA and preserves existing peer/profile data", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "magi-cold-peer-"));
  const previous = { home: process.env.MAGI_HOME, os: process.env.MAGI_OS_HOME };
  process.env.MAGI_HOME = path.join(root, "profile"); process.env.MAGI_OS_HOME = path.join(root, "os");
  const existing = new Bus(SHELL_MAGI_HANDLE, path.join(process.env.MAGI_HOME, "shell"));
  existing.contacts.update(MAGI_CONTACT_ID, { nickname: "preserved nickname" });
  existing.contacts.savePeer(existing.contacts.forPeer({ principal: "a".repeat(64), endpoint: "https://peer.example" }).id);
  existing.close();
  let spawned = 0;
  const shell = new Shell({ paths: { home: process.env.MAGI_OS_HOME, userData: path.join(root, "electron"), checkout: repository }, repository: "https://github.com/realTaki/MAGI.git", managed: false,
    tools: { git: "git", node: process.execPath, npm: path.join(repository, "apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js"), env: process.env },
    emit() {}, async openExternal() {}, copy() {}, spawn() { spawned++; throw new Error("startup must not create another EVA"); } });
  try {
    await shell.start(); await shell.worker.methods.start(); await shell.worker.methods.start();
    assert.equal(spawned, 0);
    assert.equal(shell.bus.contacts.get(MAGI_CONTACT_ID)?.nickname, "preserved nickname");
    assert.equal(shell.bus.contacts.list().filter(contact => Boolean(contact.peer_id)).length, 1);
    const contacts = shell.worker.methods["peer.contacts"]();
    assert.equal(contacts.filter(contact => contact.id === "shell").length, 1);
    assert.equal(contacts[0].running, true);
    assert.match(shell.peer.origin!, /^http:\/\/127\.0\.0\.1:/);
  } finally {
    await shell.stop(true);
    for (const [key, value] of [["MAGI_HOME", previous.home], ["MAGI_OS_HOME", previous.os]]) { if (value === undefined) delete process.env[key!]; else process.env[key!] = value; }
    await rm(root, { recursive: true, force: true });
  }
});

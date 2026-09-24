import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { createServer } from "node:net";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { DatabaseSync } from "node:sqlite";
import { createApp } from "../server/app.ts";
import { ProcessSpawner } from "../server/spawn.ts";
import { isRecord, request, tempRoot } from "./helpers.ts";

const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const bun = path.join(repository, "shell", "runtime", "bin", process.platform === "win32" ? "bun.exe" : "bun");

async function freePort(): Promise<number> {
  const server = createServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (typeof address === "string" || !address) throw new Error("could not reserve a port");
  await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  return address.port;
}

async function waitFor(check: () => Promise<boolean>): Promise<void> {
  const deadline = Date.now() + 12_000;
  while (Date.now() < deadline) {
    if (await check()) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("timed out waiting for the spawned TypeScript MAGI");
}

test("ASP spawns a TypeScript MAGI that connects, persists nickname, and joins a group", { skip: !existsSync(bun), timeout: 20_000 }, async (t) => {
  const root = tempRoot(t);
  const previousHome = process.env.HOME;
  const previousBun = process.env.MAGI_BUN;
  process.env.HOME = path.join(root, "home");
  process.env.MAGI_BUN = bun;
  t.after(() => {
    if (previousHome === undefined) delete process.env.HOME; else process.env.HOME = previousHome;
    if (previousBun === undefined) delete process.env.MAGI_BUN; else process.env.MAGI_BUN = previousBun;
  });

  const port = await freePort();
  const app = createApp({
    databasePath: path.join(root, "asp.sqlite"),
    aspBase: `http://127.0.0.1:${port}`,
    magiSpawner: new ProcessSpawner(),
  });
  await app.listen("127.0.0.1", port);
  try {
    const operator = await request(app, "GET", "/operator");
    assert.ok(isRecord(operator.data) && typeof operator.data.token === "string");
    const token = operator.data.token;
    const created = await request(app, "POST", "/conversations", { token, body: { kind: "bot" } });
    assert.equal(created.status, 201);
    assert.ok(isRecord(created.data));
    assert.equal(created.data.spawned, true);
    const handle = String((created.data.agents as string[])[0]);
    await waitFor(async () => {
      const listed = await request(app, "GET", "/bots", { token });
      return isRecord(listed.data) && Array.isArray(listed.data.bots) && listed.data.bots.some((row) => isRecord(row) && row.handle === handle && row.online === true);
    });

    const renamed = await request(app, "PATCH", `/bots/${encodeURIComponent(handle)}/nickname`, { token, body: { nickname: "司空" } });
    assert.equal(renamed.status, 200);
    const workspaceDb = path.join(root, "home", ".magi", "eva-000", "memories", "magi.db");
    const workspace = new DatabaseSync(workspaceDb, { readOnly: true });
    try {
      assert.equal((workspace.prepare("SELECT nickname FROM books_contacts WHERE id = 1").get() as { nickname: string }).nickname, "司空");
    } finally { workspace.close(); }

    const group = await request(app, "POST", "/conversations", { token, body: { kind: "group" } });
    assert.ok(isRecord(group.data));
    const groupId = String(group.data.conversation_id);
    const added = await request(app, "POST", `/conversations/${groupId}/members`, { token, body: { handle } });
    assert.equal(added.status, 200);
    await waitFor(async () => {
      const view = await request(app, "GET", `/conversations/${groupId}`, { token });
      return isRecord(view.data) && Array.isArray(view.data.participants) && view.data.participants.some((row) => isRecord(row) && row.handle === handle && row.status === "joined");
    });
  } finally {
    await app.close();
  }
});

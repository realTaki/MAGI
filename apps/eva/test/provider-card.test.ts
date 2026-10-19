import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { Magi } from "../eva.js";
import { encryptFor, type CardSpec } from "@magi/bus";
import { DatabaseSync } from "node:sqlite";
import { PROVIDER_CATALOG } from "@magi/bus/jobs/providerCatalog.js";
import { builtinModels } from "@earendil-works/pi-ai/providers/all";

test("every offered model matches the installed backend's ID, endpoint, protocol and context", () => {
  const models = builtinModels();
  for (const provider of PROVIDER_CATALOG) for (const entry of provider.models) {
    const actual = models.getModel(provider.id === "minimax-global" ? "minimax" : provider.id, entry.id);
    assert.ok(actual, `${provider.id}/${entry.id}`);
    assert.equal(actual.baseUrl, provider.baseUrl);
    assert.equal(actual.api, provider.api);
    assert.equal(actual.contextWindow, entry.contextWindow);
  }
});

test("provider cards survive repeated failures, direct correction, cancel and replay without a model", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-provider-card-"));
  let attempts = 0;
  const configured: unknown[] = [];
  const magi = new Magi("@card.magi", { workspace, client: {
    async complete() { throw new Error("configuration must not require a model turn"); },
    async verify(settings) { attempts++; if (settings.api_key !== "synthetic-good") throw new Error(`Invalid API key ${settings.api_key}`); },
    configure(settings) { configured.push(settings); },
  } });
  const bus = magi.bus;
  const chat = bus.chats.forChannel("asp", "test-chat");
  const db = new DatabaseSync(join(workspace, "logs", "jobs.db"), { readOnly: true });
  const rows = () => db.prepare("SELECT * FROM jobs").all() as unknown as Array<{ id: number; type: string; input: string }>;
  const cards = () => rows().filter((row) => row.type === "CardJob").map((row) => ({ ...row, spec: (JSON.parse(row.input) as { spec: CardSpec }).spec }));
  const send = async (body: Record<string, unknown>, kind: "card_result" | "card_reject" = "card_result") => {
    const id = bus.board("InboundSecretJob").publish({ sender: "@user.magi", chat_id: chat.id, kind, body }, "test");
    for (let i = 0; i < 500; i++) {
      const result = bus.board("InboundSecretJob").result(id);
      if (result) return result;
      await sleep(10);
    }
    throw new Error("card action timed out");
  };
  const fields = (key: string) => ({ provider: "minimax-cn", model: "MiniMax-M3", base_url: "", "provider.api_key": { ciphertext: encryptFor(bus.identity.publicKey, key) } });
  try {
    bus.settings.set("provider.card.stopped", "1");
    bus.settings.set("provider.base_url", "https://old.example/v1");
    await magi.start();
    assert.equal((await send({ id: "open-1", action: "open" })).status, "completed");
    assert.equal(cards().length, 1);
    await send({ id: "open-1", action: "open" });
    assert.equal(cards().length, 1, "duplicate open is idempotent");
    for (let i = 0; i < 3; i++) {
      const card = cards()[0];
      const attempt = `attempt-${i}`;
      assert.equal((await send({ id: card.spec.id, attempt, fields: fields(`synthetic-bad-${i}`) })).status, "failed");
      assert.equal(bus.secrets.get("provider.api_key"), null);
      assert.equal(bus.settings.get("provider.base_url"), "https://old.example/v1");
      assert.equal(JSON.parse(bus.settings.get(`card.receipt.${card.spec.id}`)!).state, "failed");
      await send({ id: card.spec.id, attempt, fields: fields(`synthetic-bad-${i}`) });
      assert.equal(attempts, i + 1, "duplicate submission cannot verify again");
      assert.equal(cards().length, 1, "failure leaves the same form editable");
    }
    const cancelled = cards().at(-1)!;
    await send({ id: cancelled.spec.id }, "card_reject");
    assert.equal(JSON.parse(bus.settings.get(`card.receipt.${cancelled.spec.id}`)!).state, "cancelled");
    await send({ id: "open-after-cancel", action: "open" });
    const good = cards().at(-1)!;
    assert.equal((await send({ id: good.spec.id, attempt: "good", fields: fields("synthetic-good") })).status, "completed");
    assert.equal(bus.settings.get("provider.name"), "minimax-cn");
    assert.equal(bus.settings.get("provider.model"), "MiniMax-M3");
    assert.equal(bus.settings.get("provider.base_url"), "");
    assert.equal(bus.secrets.get("provider.api_key"), "synthetic-good");
    assert.equal(JSON.parse(bus.settings.get(`card.receipt.${good.spec.id}`)!).state, "saved");
    await send({ id: good.spec.id, attempt: "good", fields: fields("synthetic-good") });
    assert.equal(attempts, 4);
    assert.equal(configured.length, 1);
    await send({ id: "open-after-save", action: "open" });
    assert.deepEqual(cards().at(-1)!.spec.values, { provider: "minimax-cn", model: "MiniMax-M3", base_url: "" });
    const publicData = JSON.stringify(rows().filter((row) => row.type === "MessageDeliveryJob" || row.type === "CardJob"));
    assert.ok(!publicData.includes("synthetic-good") && !publicData.includes("synthetic-bad"));
  } finally {
    db.close();
    await magi.stop();
    await rm(workspace, { recursive: true, force: true });
  }
});

import test from "node:test";
import assert from "node:assert/strict";
import { initialProviderValues, changeProvider, cardStateFromEvents, cardWithReceipt } from "../web/src/provider-form.ts";
import { providerCard } from "@magi/bus/jobs/card.js";

test("provider defaults preserve public existing values, flag legacy models, and reset vendor state", () => {
  const card = providerCard("@test.magi", { provider: "minimax-cn", model: "MinimaxM3", base_url: "https://stale.example" });
  assert.deepEqual(initialProviderValues(card), { provider: "minimax-cn", model: "MinimaxM3", base_url: "" });
  assert.deepEqual(changeProvider("minimax-cn"), { provider: "minimax-cn", model: "MiniMax-M3", base_url: "", "provider.api_key": "" });
  assert.deepEqual(changeProvider("custom"), { provider: "custom", model: "", base_url: "", "provider.api_key": "" });
  assert.equal(changeProvider("minimax-global").model, "MiniMax-M3");
  assert.equal(initialProviderValues(providerCard("@test.magi", { provider: "custom", model: "local", base_url: "http://localhost:8080/v1" })).base_url, "http://localhost:8080/v1");
});

test("card acknowledgement survives event replay and only its owner can confirm saving", () => {
  const card = providerCard("@test.magi");
  const submission = { payload: { sender: "@user.magi", metadata: { card_result: { id: card.id } } } };
  const status = (sender: string, state: string) => ({ payload: { sender, metadata: { card_status: { id: card.id, state } } } });
  assert.equal(cardStateFromEvents(card, [submission]), "pending");
  assert.equal(cardStateFromEvents(card, [submission, status("@peer.magi", "saved")]), "pending");
  for (const state of ["saved", "failed", "cancelled"] as const) {
    assert.equal(cardStateFromEvents(card, [submission, status(card.handle, state), submission]), state);
  }
});

test("a rehydrated card uses acknowledged public fields and rejects credential fields", () => {
  const card = providerCard("@test.magi");
  const updated = cardWithReceipt(card, [{ payload: { sender: card.handle, metadata: { card_status: { id: card.id, state: "saved", values: { provider: "minimax-cn", model: "MiniMax-M3", api_key: "synthetic-must-not-copy" } } } } }]);
  assert.deepEqual(updated.values, { provider: "minimax-cn", model: "MiniMax-M3" });
  assert.ok(!JSON.stringify(updated).includes("synthetic-must-not-copy"));
});

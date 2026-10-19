import test from "node:test";
import assert from "node:assert/strict";
import { initialProviderValues, changeProvider, cardReceiptFromEvents, cardWithReceipt, providerProfileHref, providerProfileHandle } from "../web/src/provider-form.ts";
import { providerCard } from "@magi/bus/jobs/card.js";

test("provider defaults use exact supported IDs and clear another vendor's fields", () => {
  const card = providerCard("@test.magi", { provider: "minimax-cn", model: "MinimaxM3", base_url: "https://stale.example" });
  assert.deepEqual(initialProviderValues(card), { provider: "minimax-cn", model: "MinimaxM3", base_url: "" });
  assert.deepEqual(changeProvider("minimax-cn"), { provider: "minimax-cn", model: "MiniMax-M3", base_url: "", "provider.api_key": "" });
  assert.deepEqual(changeProvider("custom"), { provider: "custom", model: "", base_url: "", "provider.api_key": "" });
});
test("per-attempt acknowledgements survive stale replay, direct retries and peer spoofing", () => {
  const card = providerCard("@test.magi");
  const submission = (attempt: string) => ({ payload: { sender: "@user.magi", metadata: { card_result: { id: card.id, attempt } } } });
  const status = (attempt: string, state: string, sender = card.handle) => ({ payload: { sender, metadata: { card_status: { id: card.id, attempt, state } } } });
  assert.equal(cardReceiptFromEvents(card, [submission("one")])?.state, "pending");
  assert.equal(cardReceiptFromEvents(card, [submission("one"), status("one", "saved", "@peer.magi")])?.state, "pending");
  const events = [submission("one"), status("one", "failed"), submission("two"), status("one", "failed"), submission("one")];
  assert.equal(cardReceiptFromEvents(card, events)?.state, "pending");
  assert.equal(cardReceiptFromEvents(card, [...events, status("two", "saved"), status("two", "pending")])?.state, "saved");
});
test("public receipts filter secrets and profile links bind the exact MAGI without credentials", () => {
  const card = providerCard("@test.magi");
  const updated = cardWithReceipt(card, { id: card.id, state: "saved", values: { provider: "minimax-cn", model: "MiniMax-M3", api_key: "synthetic-must-not-copy" } as any });
  assert.deepEqual(updated.values, { provider: "minimax-cn", model: "MiniMax-M3" });
  assert.ok(!JSON.stringify(updated).includes("synthetic-must-not-copy"));
  const link = providerProfileHref("@eva-002.magi");
  assert.equal(providerProfileHandle(link.slice(1)), "@eva-002.magi");
  for (const invalid of ["/magi/%ZZ/provider", "/magi/%40eva-002.magi%3Fkey%3Dsecret/provider", "/magi/%2F..%2F/provider", "/settings"]) assert.equal(providerProfileHandle(invalid), null);
});

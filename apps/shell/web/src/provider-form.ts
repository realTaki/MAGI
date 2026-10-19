import { providerPreset } from "@magi/bus/jobs/providerCatalog.js";
import type { CardSpec } from "@magi/bus";
import type { CardReceipt } from "@magi/bus/jobs/card.js";
export function isProviderCard(card: CardSpec): boolean { return card.purpose === "provider"; }
export function initialProviderValues(card: CardSpec): Record<string, string> {
  const provider = card.values?.provider || "openai";
  return { provider, model: card.values?.model || providerPreset(provider)?.models[0].id || "", base_url: provider === "custom" ? card.values?.base_url || "" : "" };
}
export function changeProvider(provider: string): Record<string, string> {
  return { provider, model: providerPreset(provider)?.models[0].id || "", base_url: "", "provider.api_key": "" };
}
type Event = { payload: { sender?: string; metadata?: Record<string, unknown> } };
export function cardReceiptFromEvents(card: CardSpec, events: Event[]): CardReceipt | undefined {
  const attempts = new Set<string>();
  for (const { payload } of events) {
    const result = payload.metadata?.card_result as { id?: string; attempt?: string } | undefined;
    if (payload.sender === "@user.magi" && result?.id === card.id && result.attempt) attempts.add(result.attempt);
  }
  const latest = [...attempts].at(-1);
  let receipt: CardReceipt | undefined;
  for (const { payload } of events) {
    const status = payload.metadata?.card_status as CardReceipt | undefined;
    if (payload.sender !== card.handle || status?.id !== card.id || !["pending", "saved", "failed", "cancelled"].includes(status.state)) continue;
    if (latest && status.attempt !== latest && status.state !== "cancelled") continue;
    if (receipt && receipt.state !== "pending" && status.state === "pending") continue;
    receipt = status;
  }
  return receipt ?? (latest ? { id: card.id, state: "pending", attempt: latest } : undefined);
}
export function cardWithReceipt(card: CardSpec, receipt?: CardReceipt): CardSpec {
  if (!receipt?.values) return card;
  const values: NonNullable<CardSpec["values"]> = {};
  for (const name of ["provider", "model", "base_url"] as const) if (typeof receipt.values[name] === "string") values[name] = receipt.values[name];
  return { ...card, values };
}
export function providerProfileHref(handle: string): string { return `#/magi/${encodeURIComponent(handle)}/provider`; }
export function providerProfileHandle(path: string): string | null {
  const match = /^\/magi\/([^/]+)\/provider$/.exec(path);
  if (!match) return null;
  try { const handle = decodeURIComponent(match[1]); return /^@[a-zA-Z0-9_.-]+\.magi$/.test(handle) ? handle : null; } catch { return null; }
}

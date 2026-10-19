import { providerPreset } from "@magi/bus/jobs/providerCatalog.js";
import type { CardSpec } from "@magi/bus";

export function isProviderCard(card: CardSpec): boolean {
  return card.purpose === "provider" || ["provider", "model", "provider.api_key"].every((name) => card.fields.some((field) => field.name === name));
}

/** Only public defaults cross the card boundary. Secrets are never prefilled. */
export function initialProviderValues(card: CardSpec): Record<string, string> {
  const provider = card.values?.provider === "claude" ? "anthropic" : card.values?.provider || "openai";
  return {
    provider,
    model: card.values?.model || providerPreset(provider)?.models[0].id || "",
    base_url: provider === "custom" ? card.values?.base_url || "" : "",
  };
}

export function changeProvider(provider: string): Record<string, string> {
  // Clearing the key too prevents submitting the previous vendor's credential to a new host.
  return { provider, model: providerPreset(provider)?.models[0].id || "", base_url: "", "provider.api_key": "" };
}

export type CardState = "pending" | "saved" | "failed" | "cancelled";
export function cardStateFromEvents(card: CardSpec, events: Array<{ payload: { sender?: string; metadata?: Record<string, unknown> } }>): CardState | undefined {
  let state: CardState | undefined;
  for (const { payload } of events) {
    const meta = payload.metadata;
    if (!meta) continue;
    const status = meta.card_status as { id?: string; state?: string } | undefined;
    // A peer cannot acknowledge a card belonging to another MAGI.
    if (payload.sender === card.handle && status?.id === card.id && ["saved", "failed", "cancelled"].includes(status.state ?? "")) state = status.state as CardState;
    const operator = ["@user.magi", "@user", "user"].includes(payload.sender ?? "");
    const result = meta.card_result as { id?: string; action?: string } | undefined;
    const reject = meta.card_reject as { id?: string } | undefined;
    if (!state && operator && (result?.id === card.id || reject?.id === card.id)) state = "pending";
  }
  return state;
}

/** Rehydrating a terminal card shows the submitted public selection, never a secret. */
export function cardWithReceipt(card: CardSpec, events: Array<{ payload: { sender?: string; metadata?: Record<string, unknown> } }>): CardSpec {
  let values = card.values;
  for (const { payload } of events) {
    const status = payload.metadata?.card_status as { id?: string; values?: Record<string, unknown> } | undefined;
    if (payload.sender !== card.handle || status?.id !== card.id || !status.values) continue;
    values = {};
    for (const name of ["provider", "model", "base_url"] as const) {
      if (typeof status.values[name] === "string") values[name] = status.values[name];
    }
  }
  return { ...card, values };
}

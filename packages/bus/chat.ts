/** The conversational boundary shared by embedded EVA and channel adapters.
 * Identity comes from the authenticated adapter, never from message content.
 */
import type { Bus } from "./bus.js";
import { MAGI_CONTACT_ID, SYSTEM_CONTACT_ID } from "./books/contactBook.js";
import { ASP_MAGI_HANDLE, EVA_MAGI_HANDLE, SHELL_MAGI_HANDLE } from "./identity.js";
import { messageDelivery, type MessageDeliveryJob } from "./jobs/messageDelivery.js";

export type SealedField = { ciphertext: string };
export type CardSubmission = {
  id: string;
  action?: "open" | "status";
  attempt?: string;
  fields?: Record<string, string | SealedField>;
};
export type ProviderEnvelope = {
  kind: "provider.sync";
  ciphertext: string;
  provider?: string;
  model?: string;
  base_url?: string;
};
export type ChatInput = {
  content: string;
  metadata?: {
    card_result?: CardSubmission;
    card_reject?: { id: string };
    envelope?: ProviderEnvelope;
  };
};
/** Public delivery context lets an embedded host route output without reading BUS. */
export type ChatDelivery = MessageDeliveryJob & { chat_id: number; address: string | null };
export type ChatReceipt = { type: "MessageDeliveryJob" | "InboundSecretJob"; id: number };

/** Reserved structured fields are handled without a model, even before bootstrap.
 * Their content is never copied to ordinary history: record only fixed summaries.
 */
export function chat(bus: Bus, chat_id: number, contact_id: number, input: ChatInput, publisher = "channel"): ChatReceipt {
  if (!bus.chats.get(chat_id)) throw new Error("chat does not exist");
  const contact = bus.contacts.get(contact_id);
  if (!contact || contact_id === MAGI_CONTACT_ID) throw new Error("chat requires an authenticated sender");
  if (typeof input.content !== "string") throw new Error("chat content must be text");
  const metadata = input.metadata;
  if (metadata !== undefined && !record(metadata)) throw new Error("chat metadata must be an object");
  const kinds = ["card_result", "card_reject", "envelope"].filter(key => metadata && key in metadata);
  if (kinds.length > 1) throw new Error("chat must contain only one structured action");
  if (kinds.length) {
    const kind = kinds[0];
    const body = (metadata as Record<string, unknown>)[kind];
    if (!record(body)) throw new Error("invalid structured chat");
    const owner = contact_id === SYSTEM_CONTACT_ID;
    if (kind !== "envelope" && !owner) throw new Error("only the owner may submit a card");
    if (kind === "envelope") {
      // Being seen in a chat or choosing a .magi handle does not confer trust.
      const trusted = [SHELL_MAGI_HANDLE, ASP_MAGI_HANDLE, EVA_MAGI_HANDLE].includes(contact.asp_handle ?? "");
      if (!owner && !trusted) throw new Error("provider sync requires a trusted sender");
      if (body.kind !== "provider.sync" || typeof body.ciphertext !== "string" || !body.ciphertext) throw new Error("invalid provider envelope");
      for (const key of ["provider", "model", "base_url"]) if (body[key] !== undefined && typeof body[key] !== "string") throw new Error("invalid provider field");
      if (Object.keys(body).some(key => !["kind", "ciphertext", "provider", "model", "base_url"].includes(key))) throw new Error("unknown provider field");
    } else {
      if (typeof body.id !== "string" || !body.id) throw new Error("card is missing an id");
      const allowed = kind === "card_reject" ? ["id"] : ["id", "action", "attempt", "fields"];
      if (Object.keys(body).some(key => !allowed.includes(key))) throw new Error("unknown card field");
      if (body.action !== undefined && body.action !== "open" && body.action !== "status") throw new Error("invalid card action");
      if (body.attempt !== undefined && typeof body.attempt !== "string") throw new Error("invalid card attempt");
      if (body.fields !== undefined) {
        if (!record(body.fields)) throw new Error("invalid card fields");
        for (const [key, value] of Object.entries(body.fields)) {
          if (key === "provider.api_key" && (!record(value) || typeof value.ciphertext !== "string" || !value.ciphertext)) throw new Error("provider key must be sealed");
          if (typeof value !== "string" && (!record(value) || typeof value.ciphertext !== "string" || Object.keys(value).some(key => key !== "ciphertext"))) throw new Error("invalid card field value");
        }
      }
    }
    const type = kind === "envelope" ? "provider.sync" : kind as "card_result" | "card_reject";
    const id = bus.board("InboundSecretJob").publish({ sender: contact.asp_handle ?? contact.name, chat_id, kind: type, body }, publisher);
    messageDelivery.record(bus, chat_id, type === "provider.sync" ? "provider sync received" : type === "card_reject" ? "card rejected" : "card submitted", contact_id);
    return { type: "InboundSecretJob", id };
  }
  return { type: "MessageDeliveryJob", id: messageDelivery.send(bus, chat_id, input.content, contact_id, publisher) };
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

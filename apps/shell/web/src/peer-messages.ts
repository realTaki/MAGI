import type { ChatMessage } from "./chat-model.ts";
import type { PeerContact, PeerConversation, PeerEvent, PeerProfile } from "./peer.ts";
import { cardReceiptFromEvents, cardWithReceipt } from "./provider-form.ts";

/** Only an authenticated local owner session can render actionable provider cards. */
export function peerMessages(contact: PeerContact, events: PeerEvent[], profile?: PeerProfile): ChatMessage[] {
  const owner = !contact.group && contact.local && profile?.owner === true && profile.handle === contact.handle;
  const receipts = events.map((event) => ({ payload: {
    sender: event.role === "user" ? "@user.magi" : contact.handle,
    metadata: event.metadata,
  } }));
  const cards = new Set<string>();
  return events.flatMap((event): ChatMessage[] => {
    const metadata = event.metadata;
    if (metadata?.card_status || metadata?.card_result || metadata?.card_reject) return [];
    const card = metadata?.card;
    if (card && event.role === "assistant" && card.handle === contact.handle && owner) {
      if (cards.has(card.id)) return [];
      cards.add(card.id);
      const receipt = cardReceiptFromEvents(card, receipts);
      return [{ type: "form", text: event.content, chatId: contact.id, card: cardWithReceipt(card, receipt), receipt }];
    }
    return event.role === "user" ? [{ type: "user", text: event.content }]
      : [{ type: "bot", text: event.content, ...(contact.group && event.sender ? { sender: event.sender } : {}) }];
  });
}

export function contactLabel(contact: PeerContact): string {
  return contact.nickname || contact.name || contact.handle;
}

export function conversationContact(conversation: PeerConversation): PeerContact {
  return { ...conversation, handle: "", name: conversation.title || conversation.id.slice("conversation:".length), nickname: "", url: "" };
}

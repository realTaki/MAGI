import { getPeerProfile, listPeerEvents, type PeerContact, type PeerEvent, type PeerProfile } from "./peer.ts";

/** A stopped owned EVA cannot answer owner RPCs; Shell itself remains reachable. */
export function isStoppedLocalEva(contact: PeerContact): boolean {
  return contact.local && contact.id !== "shell" && contact.running === false && contact.online !== true;
}

export function providerProfileState(contact: PeerContact, profile?: PeerProfile): "group" | "remote" | "offline" | "unavailable" | "ready" {
  if (contact.group) return "group";
  if (!contact.local) return "remote";
  if (isStoppedLocalEva(contact)) return "offline";
  return profile?.owner && profile.handle === contact.handle ? "ready" : "unavailable";
}

export async function loadContactDetails(contact: PeerContact): Promise<{ id: string; history?: PeerEvent[]; details?: PeerProfile; failed: boolean }> {
  if (isStoppedLocalEva(contact)) return { id: contact.id, failed: false };
  const [history, details] = await Promise.allSettled([
    listPeerEvents(contact.id),
    contact.group ? Promise.resolve(undefined) : getPeerProfile(contact.id),
  ]);
  return {
    id: contact.id,
    history: history.status === "fulfilled" ? history.value : undefined,
    details: details.status === "fulfilled" ? details.value : undefined,
    failed: history.status === "rejected" || details.status === "rejected",
  };
}

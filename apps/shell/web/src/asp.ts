/** Operator client for asp. The UI only requests; the app runs MAGI. */

const ASP_BASE = "http://127.0.0.1:42069";

/** The first thing said in a new chat, in the operator's name. */
const GREETING = "Hi";

export type Operator = {
  handle: string;
  token: string;
};

export type CreatedChat = {
  chat_id: string;
  kind: "bot" | "group";
  agents: string[];
  participants?: Array<{ handle: string; status: "invited" | "joined" | "left" }>;
  /** ASP-assigned MAGI name (`eva-000`, …). Present on `kind: "bot"`. */
  name?: string;
  topic?: string;
  description?: string;
  created_at?: number;
};

export type CardField = {
  name: string;
  label: string;
  kind: "text" | "secret" | "choice";
  choices?: string[];
  optional?: boolean;
};

export type CardSpec = {
  id: string;
  title: string;
  handle: string;
  submit: string;
  fields: CardField[];
};

export type AspEvent = {
  type: string;
  sequence: number;
  event_id: string;
  payload: {
    sender?: string;
    content?: unknown;
    metadata?: { card?: CardSpec };
  };
};

export type AspBot = {
  handle: string;
  name: string;
  online: boolean;
  in_chat?: boolean;
};

/** A user message that is safely held by the desktop until ASP accepts it. */
export type OutgoingMessage = {
  id: string;
  chatId: string;
  content: string;
  createdAt: number;
};

let operator: Operator | null = null;
// Browser previews do not expose the desktop store. Keep their outbox useful
// for the lifetime of the preview while Electron persists the real one.
let volatileOutgoing: OutgoingMessage[] = [];

async function getOperator(): Promise<Operator | null> {
  if (operator) {
    return operator;
  }
  try {
    const response = await fetch(`${ASP_BASE}/operator`, {
      signal: AbortSignal.timeout(1500),
    });
    if (!response.ok) {
      return null;
    }
    operator = (await response.json()) as Operator;
    return operator;
  } catch {
    return null;
  }
}

export function clearOperator(): void {
  operator = null;
}

export async function listAspChats(): Promise<CreatedChat[]> {
  const creds = await getOperator();
  if (!creds) return [];
  const response = await fetch(`${ASP_BASE}/chats`, {
    headers: { Authorization: `Bearer ${creds.token}` },
  });
  if (!response.ok) throw new Error(`ASP chats: ${response.status}`);
  return ((await response.json()) as { chats: CreatedChat[] }).chats;
}

export async function listAspEvents(chatId: string, afterSequence?: number): Promise<AspEvent[]> {
  const creds = await getOperator();
  if (!creds) return [];
  const url = new URL(`${ASP_BASE}/chats/${chatId}/events`);
  if (afterSequence !== undefined && afterSequence >= 0) {
    url.searchParams.set("after_sequence", String(afterSequence));
  }
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${creds.token}` },
  });
  if (!response.ok) throw new Error(`ASP events: ${response.status}`);
  return ((await response.json()) as { events: AspEvent[] }).events;
}

export async function ackAspEvents(chatId: string, events: AspEvent[]): Promise<void> {
  if (events.length === 0) return;
  const creds = await getOperator();
  if (!creds) throw new Error("ASP is unavailable");
  const response = await fetch(`${ASP_BASE}/chats/${chatId}/events/ack`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${creds.token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ event_ids: events.map((event) => event.event_id) }),
  });
  if (!response.ok) throw new Error(`ASP acknowledge: ${response.status}`);
}

async function localChat<T>(method: string, payload?: unknown): Promise<T | null> {
  const invoke = window.magiDesktop?.invokeLocal;
  return invoke ? (await invoke(method, payload)) as T : null;
}

export async function storedChats(): Promise<CreatedChat[]> {
  return await localChat<CreatedChat[]>("chat.listChats") ?? [];
}

export async function saveChats(rows: CreatedChat[]): Promise<void> {
  await localChat("chat.saveChats", rows);
}

export async function storedEvents(id: string): Promise<AspEvent[]> {
  return await localChat<AspEvent[]>("chat.listEvents", id) ?? [];
}

export async function lastStoredSequence(id: string): Promise<number> {
  return await localChat<number>("chat.lastSequence", id) ?? -1;
}

export async function saveEvents(id: string, events: AspEvent[]): Promise<number | null> {
  return await localChat<number>("chat.saveEvents", { id, events });
}

export async function pendingAcks(id: string): Promise<AspEvent[]> {
  return await localChat<AspEvent[]>("chat.pendingAcks", id) ?? [];
}

export async function markAcknowledged(id: string, sequence: number): Promise<void> {
  await localChat("chat.markAcknowledged", { id, sequence });
}

export async function storedOutgoingMessages(): Promise<OutgoingMessage[]> {
  const invoke = window.magiDesktop?.invokeLocal;
  return invoke
    ? (await invoke("chat.listOutgoingMessages")) as OutgoingMessage[]
    : volatileOutgoing;
}

export async function queueOutgoingMessage(message: OutgoingMessage): Promise<void> {
  const invoke = window.magiDesktop?.invokeLocal;
  if (invoke) {
    await invoke("chat.queueOutgoingMessage", message);
  } else {
    volatileOutgoing = [...volatileOutgoing, message];
  }
}

export async function removeOutgoingMessage(id: string): Promise<void> {
  const invoke = window.magiDesktop?.invokeLocal;
  if (invoke) {
    await invoke("chat.removeOutgoingMessage", id);
  } else {
    volatileOutgoing = volatileOutgoing.filter((message) => message.id !== id);
  }
}

/** Save ASP's available events before acknowledging any of them. */
export async function syncAspEvents(id: string): Promise<AspEvent[]> {
  const incoming = await listAspEvents(id, await lastStoredSequence(id));
  const savedThrough = await saveEvents(id, incoming);
  if (savedThrough === null) return incoming;
  const pending = await pendingAcks(id);
  if (pending.length > 0) {
    try {
      await ackAspEvents(id, pending);
      await markAcknowledged(id, pending.at(-1)!.sequence);
    } catch {
      // The local copy is durable; retry the receipt when ASP is available.
    }
  }
  return await storedEvents(id);
}

export async function sendAspMessage(chatId: string, text: string, metadata?: Record<string, unknown>): Promise<void> {
  const creds = await getOperator();
  if (!creds) throw new Error("ASP is unavailable");
  const response = await fetch(`${ASP_BASE}/chats/${chatId}/messages`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${creds.token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(metadata ? { content: text, metadata } : { content: text }),
  });
  if (!response.ok) throw new Error(`ASP send: ${response.status}`);
}

export async function listProfiles(): Promise<Array<{ handle: string; public_key: string | null }>> {
  const creds = await getOperator();
  if (!creds) throw new Error("ASP is unavailable");
  const response = await fetch(`${ASP_BASE}/profiles`, {
    headers: { Authorization: `Bearer ${creds.token}` },
  });
  if (!response.ok) throw new Error(`ASP profiles: ${response.status}`);
  const body = await response.json() as { profiles?: Array<{ handle?: string; public_key?: string | null }> };
  return (body.profiles ?? []).flatMap((profile) =>
    typeof profile.handle === "string"
      ? [{ handle: profile.handle, public_key: typeof profile.public_key === "string" ? profile.public_key : null }]
      : [],
  );
}

/** Encrypt a card secret to the MAGI that asked for it. The plaintext stays in this form. */
export async function encryptSecret(publicKeyPem: string, plaintext: string): Promise<string> {
  const body = publicKeyPem.replace(/-----[^-]+-----/g, "").replace(/\s+/g, "");
  const binary = atob(body);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  const key = await crypto.subtle.importKey(
    "spki",
    bytes.buffer,
    { name: "RSA-OAEP", hash: "SHA-256" },
    false,
    ["encrypt"],
  );
  const cipher = await crypto.subtle.encrypt({ name: "RSA-OAEP" }, key, new TextEncoder().encode(plaintext));
  const out = new Uint8Array(cipher);
  let encoded = "";
  for (const byte of out) encoded += String.fromCharCode(byte);
  return btoa(encoded);
}

export async function updateAspNickname(handle: string, nickname: string): Promise<void> {
  const creds = await getOperator();
  if (!creds) throw new Error("ASP is unavailable");
  const response = await fetch(`${ASP_BASE}/bots/${encodeURIComponent(handle)}/nickname`, {
    method: "PATCH",
    headers: {
      Authorization: `Bearer ${creds.token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ nickname }),
  });
  if (!response.ok) {
    const result = await response.json() as { detail?: string };
    throw new Error(result.detail || `Rename failed: ${response.status}`);
  }
}

export async function createAspChat(
  kind: "bot" | "group",
): Promise<CreatedChat | null> {
  const creds = await getOperator();
  if (!creds) {
    return null;
  }
  try {
    const response = await fetch(`${ASP_BASE}/chats`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${creds.token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ kind }),
      signal: AbortSignal.timeout(8000),
    });
    if (!response.ok) {
      return null;
    }
    const chat = (await response.json()) as CreatedChat;
    await saveChats([chat]);
    // Say hello in the operator's name: it is how they see the agent answer, and how
    // the MAGI gets the chat it keeps to report trouble to.
    await sendAspMessage(chat.chat_id, GREETING).catch(() => {});
    return chat;
  } catch {
    return null;
  }
}

export async function patchAspChat(
  chatId: string,
  body: { topic?: string; description?: string },
): Promise<void> {
  const creds = await getOperator();
  if (!creds) {
    return;
  }
  try {
    await fetch(`${ASP_BASE}/chats/${chatId}`, {
      method: "PATCH",
      headers: {
        Authorization: `Bearer ${creds.token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(4000),
    });
  } catch {
    // In-place edit still applies locally.
  }
}

export async function listAspBots(chatId?: string): Promise<AspBot[]> {
  const creds = await getOperator();
  if (!creds) {
    return [];
  }
  try {
    const url = new URL("bots", `${ASP_BASE}/`);
    if (chatId) {
      url.searchParams.set("chat_id", chatId);
    }
    const response = await fetch(url, {
      headers: { Authorization: `Bearer ${creds.token}` },
      signal: AbortSignal.timeout(4000),
    });
    if (!response.ok) {
      return [];
    }
    const body = (await response.json()) as { bots?: AspBot[] };
    return body.bots ?? [];
  } catch {
    return [];
  }
}

export async function addAspChatMember(
  chatId: string,
  handle: string,
): Promise<CreatedChat | null> {
  const creds = await getOperator();
  if (!creds) {
    return null;
  }
  try {
    const response = await fetch(`${ASP_BASE}/chats/${chatId}/members`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${creds.token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ handle }),
      signal: AbortSignal.timeout(4000),
    });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as CreatedChat;
  } catch {
    return null;
  }
}

export type SourceStatus = {
  available: boolean;
  branch: string;
  /** Commit currently running from the local checkout. */
  commit: string;
  /** Latest commit reported by the checkout's origin branch. */
  latestCommit: string;
  /** Nearest release tag reachable from this checkout. */
  tag: string;
  /** GitHub owner/name for the checkout's origin, normally the operator's fork. */
  repository: string;
  /** GitHub owner/name used for the AgenticSociety comparison. */
  upstreamRepository: string;
  /** GitHub URL for latestCommit. */
  commitUrl: string;
  tagUrl: string;
  /** Commit where this checkout diverged from AgenticSociety. Empty when unknown. */
  forkPoint: string;
  forkPointUrl: string;
  remote: string;
  /** AgenticSociety has commits that are not in this checkout. */
  remoteAhead: boolean;
  /** False when the remote could not be read; local fields may still be set. */
  remoteChecked: boolean;
};

export async function getSourceStatus(): Promise<SourceStatus | null> {
  const invoke = window.magiDesktop?.invokeLocal;
  return invoke ? ((await invoke("source.status")) as SourceStatus) : null;
}

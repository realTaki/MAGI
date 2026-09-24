/** Operator client for asp. The UI only requests; the app runs MAGI. */

const ASP_BASE = "http://127.0.0.1:42069";

export type Operator = {
  handle: string;
  token: string;
};

export type CreatedConversation = {
  conversation_id: string;
  kind: "bot" | "group";
  agents: string[];
  /** ASP-assigned MAGI name (`eva-000`, …). Present on `kind: "bot"`. */
  name?: string;
  topic?: string;
  description?: string;
  created_at?: number;
};

export type AspEvent = {
  type: string;
  sequence: number;
  event_id: string;
  payload: { sender?: string; content?: unknown };
};

export type AspBot = {
  handle: string;
  name: string;
  online: boolean;
  in_conversation?: boolean;
};

let operator: Operator | null = null;

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

export async function listAspConversations(): Promise<CreatedConversation[]> {
  const creds = await getOperator();
  if (!creds) return [];
  const response = await fetch(`${ASP_BASE}/conversations`, {
    headers: { Authorization: `Bearer ${creds.token}` },
  });
  if (!response.ok) throw new Error(`ASP conversations: ${response.status}`);
  return ((await response.json()) as { conversations: CreatedConversation[] }).conversations;
}

export async function listAspEvents(conversationId: string, afterSequence?: number): Promise<AspEvent[]> {
  const creds = await getOperator();
  if (!creds) return [];
  const url = new URL(`${ASP_BASE}/sessions/${conversationId}/events`);
  if (afterSequence !== undefined && afterSequence >= 0) {
    url.searchParams.set("after_sequence", String(afterSequence));
  }
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${creds.token}` },
  });
  if (!response.ok) throw new Error(`ASP events: ${response.status}`);
  return ((await response.json()) as { events: AspEvent[] }).events;
}

export async function ackAspEvents(conversationId: string, events: AspEvent[]): Promise<void> {
  if (events.length === 0) return;
  const creds = await getOperator();
  if (!creds) throw new Error("ASP is unavailable");
  const response = await fetch(`${ASP_BASE}/sessions/${conversationId}/events/ack`, {
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

export async function storedConversations(): Promise<CreatedConversation[]> {
  return await localChat<CreatedConversation[]>("chat.listConversations") ?? [];
}

export async function saveConversations(rows: CreatedConversation[]): Promise<void> {
  await localChat("chat.saveConversations", rows);
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

export async function sendAspMessage(conversationId: string, text: string): Promise<void> {
  const creds = await getOperator();
  if (!creds) throw new Error("ASP is unavailable");
  const response = await fetch(`${ASP_BASE}/sessions/${conversationId}/messages`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${creds.token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ content: text }),
  });
  if (!response.ok) throw new Error(`ASP send: ${response.status}`);
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

export async function createAspConversation(
  kind: "bot" | "group",
): Promise<CreatedConversation | null> {
  const creds = await getOperator();
  if (!creds) {
    return null;
  }
  try {
    const response = await fetch(`${ASP_BASE}/conversations`, {
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
    const conversation = (await response.json()) as CreatedConversation;
    await saveConversations([conversation]);
    return conversation;
  } catch {
    return null;
  }
}

export async function patchAspConversation(
  conversationId: string,
  body: { topic?: string; description?: string },
): Promise<void> {
  const creds = await getOperator();
  if (!creds) {
    return;
  }
  try {
    await fetch(`${ASP_BASE}/conversations/${conversationId}`, {
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

export async function listAspBots(conversationId?: string): Promise<AspBot[]> {
  const creds = await getOperator();
  if (!creds) {
    return [];
  }
  try {
    const url = new URL("bots", `${ASP_BASE}/`);
    if (conversationId) {
      url.searchParams.set("conversation_id", conversationId);
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

export async function addAspConversationMember(
  conversationId: string,
  handle: string,
): Promise<CreatedConversation | null> {
  const creds = await getOperator();
  if (!creds) {
    return null;
  }
  try {
    const response = await fetch(`${ASP_BASE}/conversations/${conversationId}/members`, {
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
    return (await response.json()) as CreatedConversation;
  } catch {
    return null;
  }
}

export type ProviderSettings = {
  provider: string | null;
  model: string | null;
  api_key: string | null;
  base_url: string | null;
};

/** What the app saved locally, plus which MAGI confirmed the broadcast. */
export type ProviderSettingsSaved = ProviderSettings & {
  synced: string[];
  failed: { handle: string; detail: string }[];
};

export type ProviderUsage = {
  provider: string | null;
  status: "available" | "unsupported" | "unconfigured" | "error";
  available?: boolean;
  balances: Array<{ currency: string; total: string }>;
  message: string;
};

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

export async function getProviderSettings(): Promise<ProviderSettings | null> {
  const invoke = window.magiDesktop?.invokeLocal;
  return invoke ? (await invoke("provider.settings")) as ProviderSettings : null;
}

export async function getProviderCatalog(): Promise<Record<string, { id: string; name: string }[]>> {
  const invoke = window.magiDesktop?.invokeLocal;
  return invoke ? (await invoke("provider.catalog")) as Record<string, { id: string; name: string }[]> : {};
}

export async function getProviderUsage(): Promise<ProviderUsage> {
  const invoke = window.magiDesktop?.invokeLocal;
  if (!invoke) {
    return { provider: null, status: "error", balances: [], message: "Desktop backend unavailable" };
  }
  return (await invoke("provider.usage")) as ProviderUsage;
}

export async function saveProviderSettings(
  input: ProviderSettings,
): Promise<ProviderSettingsSaved> {
  const invoke = window.magiDesktop?.invokeLocal;
  if (!invoke) throw new Error("The desktop app is unavailable");
  return (await invoke("provider.save", input)) as ProviderSettingsSaved;
}

/** Operator client for the local (intranet) magi-asp. */

const ASP_BASE = import.meta.env.VITE_MAGI_ASP_URL ?? "http://127.0.0.1:42069";

export type Operator = {
  handle: string;
  token: string;
};

export type CreatedConversation = {
  conversation_id: string;
  kind: "bot" | "group";
  agents: string[];
  spawned?: boolean;
};

export type AspBot = {
  handle: string;
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
    return (await response.json()) as CreatedConversation;
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

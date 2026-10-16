/**
 * Tell other MAGIs this one's provider. The API key leaves only as ciphertext
 * addressed to the recipient's public key, in a structured message field.
 * If they have no key yet, ask, wait for them to register one, and send again.
 */

import { encryptFor, MAGI_CONTACT_ID, messageDelivery, waitForJob, type Bus, type ExecutableTool } from "@magi/bus";

type Profile = { handle: string; public_key: string | null };

function credentials(bus: Bus): { base: string; token: string } {
  const base = bus.settings.get("asp.base")?.trim();
  const token = bus.settings.get("asp.token")?.trim();
  if (!base || !token) throw new Error("ASP is not configured");
  return { base, token };
}

async function aspJson(bus: Bus, path: string, body?: Record<string, unknown>): Promise<unknown> {
  const { base, token } = credentials(bus);
  const response = await fetch(new URL(path, base), {
    method: body === undefined ? "GET" : "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`ASP HTTP ${response.status}: ${text.slice(0, 500)}`);
  return text === "" ? null : JSON.parse(text) as unknown;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function profiles(bus: Bus): Promise<Profile[]> {
  const body = await aspJson(bus, "/profiles");
  if (!isRecord(body) || !Array.isArray(body.profiles)) throw new Error("ASP profiles were not a list");
  return body.profiles.flatMap((row) => {
    if (!isRecord(row) || typeof row.handle !== "string") return [];
    const public_key = typeof row.public_key === "string" && row.public_key !== "" ? row.public_key : null;
    return [{ handle: row.handle, public_key }];
  });
}

async function chatWith(bus: Bus, handle: string): Promise<number> {
  const opened = await aspJson(bus, "/chats/open", { invite: [handle] });
  if (!isRecord(opened) || typeof opened.chat_id !== "string") throw new Error("ASP did not open a chat");
  return bus.chats.forChannel("asp", opened.chat_id).id;
}

function deliver(bus: Bus, chatId: number, text: string, metadata: Record<string, unknown>): void {
  messageDelivery.send(bus, chatId, text, MAGI_CONTACT_ID, "sync", metadata);
}

async function publicKey(bus: Bus, handle: string, known: string | null): Promise<string> {
  if (known) return known;
  const chatId = await chatWith(bus, handle);
  const jobId = bus.board("PeerKeyJob").publish({ handle }, "sync");
  deliver(bus, chatId, "provider sync: public key requested", { envelope: { kind: "identity.key_request" } });
  const result = await waitForJob(() => bus.board("PeerKeyJob").result(jobId), 60_000, `public key for ${handle}`);
  if (result.status === "failed" || !result.output?.public_key) {
    throw new Error(result.error ?? `${handle} did not register a public key`);
  }
  return result.output.public_key;
}

export function syncTools(bus: Bus): ExecutableTool[] {
  return [{
    name: "sync_provider",
    description: "Send this MAGI's provider to every other MAGI. The API key is encrypted to each recipient and is not written in the message.",
    input_schema: { type: "object", properties: {} },
    async run() {
      const provider = bus.settings.get("provider.name")?.trim();
      const model = bus.settings.get("provider.model")?.trim();
      const base_url = bus.settings.get("provider.base_url")?.trim() ?? "";
      const api_key = bus.secrets.get("provider.api_key");
      if (!provider || !model || !api_key) throw new Error("provider is not configured");
      const others = (await profiles(bus)).filter((profile) => profile.handle !== bus.handle);
      const synced: string[] = [];
      for (const profile of others) {
        const key = await publicKey(bus, profile.handle, profile.public_key);
        const chatId = await chatWith(bus, profile.handle);
        deliver(bus, chatId, `provider sync: ${provider}, ${model}, key updated`, {
          envelope: { kind: "provider.sync", provider, model, base_url, ciphertext: encryptFor(key, api_key) },
        });
        synced.push(profile.handle);
      }
      return JSON.stringify({ synced });
    },
  }];
}

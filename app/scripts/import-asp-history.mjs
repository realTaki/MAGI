/** Copy a running legacy ASP's visible events into desktop history without acknowledging them. */
import path from "node:path";
import { homedir } from "node:os";
import { fileURLToPath } from "node:url";
import { openChatStore } from "../main/chat-store.mjs";

export async function importAspHistory({
  asp = "http://127.0.0.1:42069",
  database = path.join(homedir(), ".magi", "app", "chat.sqlite"),
  fetcher = fetch,
} = {}) {
  async function get(endpoint, token) {
    const response = await fetcher(new URL(endpoint, asp), {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok) throw new Error(`ASP ${endpoint} returned HTTP ${response.status}`);
    return response.json();
  }

  const token = (await get("/operator")).token;
  const conversations = (await get("/conversations", token)).conversations;
  if (!Array.isArray(conversations)) throw new Error("ASP returned invalid conversations");
  const snapshots = await Promise.all(conversations.map(async (conversation) => {
    if (typeof conversation?.conversation_id !== "string") throw new Error("ASP returned a conversation without an id");
    const events = (await get(`/sessions/${encodeURIComponent(conversation.conversation_id)}/events`, token)).events;
    if (!Array.isArray(events)) throw new Error("ASP returned invalid events");
    return { conversation, events };
  }));

  const store = await openChatStore(database);
  try {
    store.saveConversations(snapshots.map(({ conversation }) => conversation));
    for (const { conversation, events } of snapshots) store.saveEvents(conversation.conversation_id, events);
  } finally {
    store.close();
  }
  return { conversations: snapshots.length, events: snapshots.reduce((total, item) => total + item.events.length, 0), database };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  const options = {};
  for (let index = 0; index < args.length; index += 2) {
    const key = args[index];
    const value = args[index + 1];
    if (!value || (key !== "--asp" && key !== "--database")) throw new Error("usage: node import-asp-history.mjs [--asp URL] [--database PATH]");
    options[key.slice(2)] = value;
  }
  importAspHistory(options).then(
    (result) => console.log(`Saved ${result.conversations} conversations and ${result.events} events to ${result.database}`),
    (error) => { console.error(error); process.exitCode = 1; },
  );
}

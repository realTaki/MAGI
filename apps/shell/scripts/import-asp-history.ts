/** Copy a running ASP's visible events into desktop history without acknowledging them. */
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Bus, SHELL_MAGI_HANDLE } from "@magi/bus";
import { aspBase, } from "@magi/runtime/asp.js";
import { magiHome } from "@magi/runtime/home.js";
import { openChatStore } from "@magi/webapp/chat-store.js";

export async function importAspHistory({
  asp = aspBase(),
  database = path.join(magiHome(), "shell", "chat.sqlite"),
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
  const chats = (await get("/chats", token)).chats;
  if (!Array.isArray(chats)) throw new Error("ASP returned invalid chats");
  const snapshots = await Promise.all(chats.map(async (chat) => {
    if (typeof chat?.chat_id !== "string") throw new Error("ASP returned a chat without an id");
    const events = (await get(`/chats/${encodeURIComponent(chat.chat_id)}/events`, token)).events;
    if (!Array.isArray(events)) throw new Error("ASP returned invalid events");
    return { chat, events };
  }));

  const bus = new Bus(SHELL_MAGI_HANDLE, path.dirname(database));
  try {
    const store = await openChatStore(database, bus);
    try {
      store.saveChats(snapshots.map(({ chat }) => chat));
      for (const { chat, events } of snapshots) store.saveEvents(chat.chat_id, events);
    } finally { store.close(); }
  } finally { bus.close(); }
  return { chats: snapshots.length, events: snapshots.reduce((total, item) => total + item.events.length, 0), database };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  const options = {};
  for (let index = 0; index < args.length; index += 2) {
    const key = args[index];
    const value = args[index + 1];
    if (!value || (key !== "--asp" && key !== "--database")) throw new Error("usage: node import-asp-history.ts [--asp URL] [--database PATH]");
    options[key.slice(2)] = value;
  }
  importAspHistory(options).then(
    (result) => console.log(`Saved ${result.chats} chats and ${result.events} events to ${result.database}`),
    (error) => { console.error(error); process.exitCode = 1; },
  );
}

import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { EVA_MAGI_HANDLE, SHELL_MAGI_HANDLE, USER_HANDLE } from "@magi/bus/identity.js";
import { ChatPage } from "../web/src/ChatPage";
import { I18nProvider } from "../web/src/i18n";

const rows = [
  { chat_id: "greeting", kind: "bot", name: "BALTHASAR", agents: [SHELL_MAGI_HANDLE], created_at: 1 },
  { chat_id: "form", kind: "bot", name: "BALTHASAR", agents: [SHELL_MAGI_HANDLE], created_at: 2 },
  { chat_id: "different-identity", kind: "bot", name: "BALTHASAR", agents: [EVA_MAGI_HANDLE], created_at: 3 },
].map((row) => ({ ...row, state: "active", participants: [
  { handle: USER_HANDLE, status: "joined" }, { handle: row.agents[0], status: "joined" },
] }));
let remoteRows = structuredClone(rows);
let rosterHandles = new Set(rows.map((row) => row.agents[0]));
const sequences: Record<string, number> = {};
const events = (id: string) => [{ type: "chat.message", sequence: sequences[id] ?? 1, event_id: `${id}-event`, payload: {
  sender: SHELL_MAGI_HANDLE, content: id === "greeting" ? "hello from Balthasar" : "[manager] worker asp recovered",
  ...(id === "form" ? { metadata: { card: {
    id: "synthetic-card", handle: SHELL_MAGI_HANDLE, purpose: "task", title: "Synthetic history form",
    submit: "Save", fields: [],
  } } } : {}),
} }];
const sends: { id: string; body: unknown }[] = [];
let saved = structuredClone(rows);
let outgoing: any[] = [];
const before = JSON.stringify(rows);
(window as any).magiDesktop = {
  aspBase: "http://synthetic.invalid",
  onLocalEvent() {},
  async invokeLocal(method: string, payload: any) {
    if (method === "chat.listChats") return structuredClone(saved);
    if (method === "chat.saveChats") { saved = structuredClone(payload); return; }
    if (method === "chat.listEvents") return events(payload);
    if (method === "chat.lastSequence") return 1;
    if (method === "chat.saveEvents") return 1;
    if (method === "chat.pendingAcks") return [];
    if (method === "chat.listOutgoingMessages") return [...outgoing];
    if (method === "chat.queueOutgoingMessage") { outgoing.push(payload); return; }
    if (method === "chat.removeOutgoingMessage") { outgoing = outgoing.filter((row) => row.id !== payload); return; }
    if (method === "chat.markAcknowledged") return;
    if (method === "github.state") return { login: "", name: "", avatar: "" };
    throw Error(`unexpected local call ${method}`);
  },
};
window.fetch = async (input, options) => {
  const url = new URL(String(input));
  if (url.pathname === "/operator") return Response.json({ handle: "@user.magi", token: "synthetic" });
  if (url.pathname === "/chats") return Response.json({ chats: remoteRows });
  if (url.pathname === "/bots") return Response.json({ bots: [...rosterHandles].map((handle) => ({ handle, online: true })) });
  if (url.pathname.endsWith("/events")) return Response.json({ events: [] });
  if (url.pathname.endsWith("/events/ack")) return Response.json({});
  if (url.pathname.endsWith("/messages")) {
    sends.push({ id: url.pathname.split("/")[2], body: JSON.parse(String(options?.body)) });
    return Response.json({});
  }
  throw Error(`unexpected fetch ${url.pathname}`);
};
localStorage.setItem("magi.locale", "en");
localStorage.setItem("magi.chats.read-through.v1", JSON.stringify({ greeting: 0, form: 0, "different-identity": 0 }));
const interval = window.setInterval.bind(window);
window.setInterval = ((handler: TimerHandler, timeout?: number, ...args: unknown[]) => interval(handler, timeout === 3000 ? 100 : timeout, ...args)) as typeof window.setInterval;
const root = createRoot(document.getElementById("root")!);
const tick = () => new Promise((resolve) => setTimeout(resolve, 50));
async function until(predicate: () => boolean) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (predicate()) return;
    await tick();
  }
  throw Error("UI did not settle");
}
function check(value: unknown, note: string) { if (!value) throw Error(note); }
const select = () => document.querySelector(".chat-page__history select") as HTMLSelectElement;
const thread = () => document.querySelector(".chat-page__thread")?.textContent ?? "";
function choose(id: string) {
  flushSync(() => { select().value = id; select().dispatchEvent(new Event("change", { bubbles: true })); });
}
async function run() {
  flushSync(() => root.render(<I18nProvider><ChatPage /></I18nProvider>));
  await until(() => Boolean(select()));
  check(document.querySelectorAll(".chat-page__bot-row").length === 2, "one entry per exact handle, not per display name or room");
  check(document.querySelector(".chat-page__bot-list")?.textContent?.includes(EVA_MAGI_HANDLE), "same names show distinct handles");
  await until(() => Boolean(select()) && thread().includes("hello from Balthasar"));
  check(select()?.options.length === 2, "both original rooms remain accessible");
  const bounds = select().getBoundingClientRect();
  check(document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2) === select(), "history selector is visible and clickable below the topbar");
  check(thread().includes("hello from Balthasar"), "greeting transcript is intact");
  await until(() => document.querySelector(`[title="${SHELL_MAGI_HANDLE}"]`)?.getAttribute("data-unread") === "true");
  choose("form");
  await until(() => Boolean(document.querySelector(".chat-page__thread .chat-page__form")));
  check(Boolean(document.querySelector(".chat-page__thread .chat-page__form")), "task form remains accessible in its own room");
  check(!thread().includes("hello from Balthasar"), "transcripts do not blend");
  await until(() => document.querySelector(`[title="${SHELL_MAGI_HANDLE}"]`)?.getAttribute("data-unread") === "false");
  const cancel = [...document.querySelectorAll<HTMLButtonElement>(".chat-page__form button")].find((button) => button.textContent === "Cancel")!;
  flushSync(() => cancel.click());
  await until(() => sends.length === 1);
  check(sends[0].id === "form" && (sends[0].body as any).metadata.card_reject.id === "synthetic-card", "card receipt targets its original chat ID");
  const composer = document.querySelector(".chat-page__composer input") as HTMLInputElement;
  flushSync(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(composer, "synthetic normal message");
    composer.dispatchEvent(new Event("input", { bubbles: true }));
  });
  flushSync(() => (document.querySelector(".chat-page__send") as HTMLButtonElement).click());
  await until(() => sends.length === 2);
  check(sends[1].id === "form" && (sends[1].body as any).content === "synthetic normal message", "ordinary messages retain the selected original chat ID");
  choose("greeting");
  await until(() => thread().includes("hello from Balthasar"));
  check(thread().includes("hello from Balthasar") && !document.querySelector(".chat-page__thread .chat-page__form"), "switching back preserves separate history");
  sequences.form = 2;
  await until(() => document.querySelector(`[title="${SHELL_MAGI_HANDLE}"]`)?.getAttribute("data-unread") === "true");
  check(select().value === "greeting", "new unread events do not switch the selected room");
  check([...select().options].find((option) => option.value === "form")?.textContent?.includes("Unread"), "unread history is identifiable in the selector");
  choose("form");
  await until(() => document.querySelector(`[title="${SHELL_MAGI_HANDLE}"]`)?.getAttribute("data-unread") === "false");
  flushSync(() => root.render(<I18nProvider key="reload"><ChatPage /></I18nProvider>));
  await until(() => Boolean(select()));
  check(document.querySelectorAll(".chat-page__bot-row").length === 2 && select().options.length === 2, "remount retains both histories in one identity entry");
  check(select().value === "form", "remount restores the selected historical room");
  check(JSON.stringify(saved) === before, "storage retains all original room records");
  remoteRows = remoteRows.map((row) => row.chat_id === "form" ? { ...row, state: "ended" } : row);
  await until(() => document.querySelectorAll(".chat-page__bot-row").length === 3);
  check(!select(), "ended room is not hidden inside the active identity entry");
  remoteRows = structuredClone(rows);
  await until(() => Boolean(select()));
  remoteRows = remoteRows.filter((row) => row.chat_id !== "form");
  await until(() => document.querySelectorAll(".chat-page__bot-row").length === 3);
  check(thread().includes("Synthetic history form"), "remote-absent room keeps its own cached history");
  remoteRows = structuredClone(rows);
  await until(() => Boolean(select()));
  rosterHandles.delete(SHELL_MAGI_HANDLE);
  await until(() => document.querySelectorAll(".chat-page__bot-row").length === 3);
  check(!select(), "unregistered handle is not treated as a current shared identity");
  rosterHandles.add(SHELL_MAGI_HANDLE);
  await until(() => Boolean(select()));
  root.unmount();
}
run().then(() => { document.body.dataset.result = "PASS"; }).catch((error) => {
  document.body.dataset.result = "FAIL";
  document.body.append(String(error.stack ?? error));
});

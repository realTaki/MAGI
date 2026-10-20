import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { ChatPage } from "../web/src/ChatPage";
import { I18nProvider } from "../web/src/i18n";
import type { PeerContact, PeerEvent } from "../web/src/peer";

const groupId = "conversation:dfd096ec-b269-4869-8193-295e5298adaf";
const contacts: PeerContact[] = [
  { id: "shell", handle: "@shell.magi", name: "Shell", nickname: "", local: true, url: "http://localhost:7000", running: true },
  { id: "local", handle: "@local.magi", name: "Same name", nickname: "", local: true, url: "http://localhost:7001", running: true },
  { id: "remote", handle: "@remote.magi", name: "Same name", nickname: "", local: false, url: "http://private-eva:7002" },
];
const form = { id: "synthetic-card", handle: "@local.magi", purpose: "task" as const, title: "Local history form", submit: "Save", fields: [] };
const histories: Record<string, PeerEvent[]> = {
  shell: [{ id: 1, role: "assistant", content: "hello from Shell", created_at: "" }],
  local: [{ id: 1, role: "assistant", content: "local history", metadata: { card: form }, created_at: "" }],
  remote: [{ id: 1, role: "assistant", content: "remote history", metadata: { card: { ...form, handle: "@remote.magi" } }, created_at: "" }],
  [groupId]: [{ id: "event-a", role: "assistant", sender: "Alice EVA", content: "shared group history", created_at: "" }],
};
const sends: Array<{ id: string; content: string; metadata?: Record<string, any> }> = [];
(window as any).magiDesktop = {
  onLocalEvent() {},
  async invokeLocal(method: string, payload: any) {
    if (method === "peer.contacts") return structuredClone(contacts);
    if (method === "peer.conversations") return [{ id: groupId, title: "Shared group", group: true, local: false, members: [{ principal: "alice", endpoint: "http://alice:7003" }] }];
    if (method === "peer.events") return structuredClone(histories[payload.id] ?? []);
    if (method === "peer.profile") return { handle: contacts.find((contact) => contact.id === payload.id)?.handle, public_key: null, owner: payload.id !== "remote" };
    if (method === "peer.send") { sends.push(structuredClone(payload)); return { accepted: true }; }
    if (method === "github.state") return { login: "", name: "", avatar: "" };
    throw Error(`unexpected local call ${method}`);
  },
};
window.fetch = async () => { throw Error("Peer UI must not call a central relay"); };
localStorage.setItem("magi.locale", "en");
localStorage.setItem("magi.peer.active", JSON.stringify("shell"));
localStorage.removeItem("magi.peer.read-events");
const timeout = window.setTimeout.bind(window);
window.setTimeout = ((handler: TimerHandler, delay?: number, ...args: unknown[]) => timeout(handler, delay === 3000 ? 50 : delay, ...args)) as typeof window.setTimeout;
const root = createRoot(document.getElementById("root")!);
const tick = () => new Promise((resolve) => timeout(resolve, 25));
async function until(predicate: () => boolean) {
  for (let attempt = 0; attempt < 160; attempt++) { if (predicate()) return; await tick(); }
  throw Error("UI did not settle");
}
function check(value: unknown, note: string) { if (!value) throw Error(note); }
const thread = () => document.querySelector(".chat-page__thread")?.textContent ?? "";
function choose(handle: string) {
  const row = [...document.querySelectorAll<HTMLButtonElement>(".chat-page__bot-row")].find((item) => item.title === handle);
  check(row, `contact ${handle} exists`);
  flushSync(() => row!.click());
}
function compose(text: string) {
  const input = document.querySelector<HTMLInputElement>(".chat-page__composer input")!;
  flushSync(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, text);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  flushSync(() => document.querySelector<HTMLButtonElement>(".chat-page__send")!.click());
}
async function run() {
  flushSync(() => root.render(<I18nProvider><ChatPage /></I18nProvider>));
  await until(() => thread().includes("hello from Shell"));
  check(document.querySelectorAll(".chat-page__bot-row").length === 4, "contacts and shared UUID group remain separate, even with identical names");
  choose("@local.magi");
  await until(() => Boolean(document.querySelector(".chat-page__thread .chat-page__form")));
  check(!thread().includes("hello from Shell"), "local transcript does not blend with Shell");
  const cancel = [...document.querySelectorAll<HTMLButtonElement>(".chat-page__form button")].find((button) => button.textContent === "Cancel")!;
  flushSync(() => cancel.click());
  await until(() => sends.length === 1);
  check(sends[0].id === "local" && sends[0].metadata?.card_reject.id === form.id, "owner card submission retains contact destination");
  compose("local normal message");
  await until(() => sends.length === 2);
  check(sends[1].id === "local" && sends[1].content === "local normal message", "ordinary message targets selected local contact");
  choose("@remote.magi");
  await until(() => thread().includes("remote history"));
  check(!document.querySelector(".chat-page__thread .chat-page__form"), "remote metadata cannot render an owner form");
  flushSync(() => document.querySelector<HTMLButtonElement>(".chat-page__name-btn")!.click());
  check(!document.querySelector(".chat-page__runtime-action"), "remote contact has no local process control");
  check(!document.querySelector("#provider"), "remote contact has no owner provider panel");
  compose("remote normal message");
  await until(() => sends.length === 3);
  check(sends[2].id === "remote", "remote message retains its contact destination");
  const group = [...document.querySelectorAll<HTMLButtonElement>(".chat-page__bot-row")].find((row) => row.textContent?.includes("Shared group"))!;
  flushSync(() => group.click());
  await until(() => thread().includes("shared group history"));
  check(thread().includes("Alice EVA"), "shared history preserves author label");
  check(Boolean(document.querySelector(".chat-page__composer input")), "group is immediately writable without approval handshake");
  compose("group normal message");
  await until(() => sends.length === 4);
  check(sends[3].id === groupId && sends[3].content === "group normal message", "group send preserves shared conversation UUID");
  choose("@shell.magi");
  histories.remote.push({ id: 2, role: "assistant", content: "new remote event", created_at: "" });
  await until(() => document.querySelector('[title="@remote.magi"]')?.textContent?.includes("new remote event") ?? false);
  check(thread().includes("hello from Shell"), "remote updates do not change active contact");
  check(Boolean(document.querySelector('[title="@remote.magi"] .chat-page__unread-dot')), "unread remote event is visible");
  choose("@remote.magi");
  await until(() => thread().includes("new remote event"));
  flushSync(() => root.render(<I18nProvider key="reload"><ChatPage /></I18nProvider>));
  await until(() => thread().includes("new remote event"));
  check(document.querySelectorAll(".chat-page__bot-row").length === 4, "remount restores contact list and selected contact history");
  root.unmount();
}
run().then(() => { document.body.dataset.result = "PASS"; }).catch((error) => {
  document.body.dataset.result = "FAIL";
  document.body.append(String(error.stack ?? error));
});

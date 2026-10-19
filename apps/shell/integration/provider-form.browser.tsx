import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { FormCardView } from "../web/src/FormCardView";
import { I18nProvider } from "../web/src/i18n";
import type { CardSpec } from "@magi/bus";

const testState = (window as any).__cardTest;
const root = createRoot(document.getElementById("root")!);
const card: CardSpec = { id: "test-card", handle: "@test.magi", purpose: "provider", title: "Provider", submit: "Save", fields: [
  { name: "provider", label: "Provider", kind: "choice" }, { name: "model", label: "Model", kind: "text" },
  { name: "base_url", label: "Base URL", kind: "text", optional: true }, { name: "provider.api_key", label: "API key", kind: "secret" },
] };
let key = 0;
function render(locale: string, values?: CardSpec["values"], state?: "saved" | "failed" | "cancelled", fresh = true) {
  if (fresh) key++;
  localStorage.setItem("magi.locale", locale);
  flushSync(() => root.render(<I18nProvider key={key}><FormCardView message={{ type: "form", text: "", chatId: "test-chat", card: { ...card, values }, state }} /></I18nProvider>));
}
function check(value: unknown, note: string) { if (!value) throw new Error(note); }
const buttons = () => [...document.querySelectorAll("button")];
const selects = () => [...document.querySelectorAll("select")];
function select(index: number, value: string) { flushSync(() => { const node = selects()[index]; node.value = value; node.dispatchEvent(new Event("change", { bubbles: true })); }); }
function password(value: string) {
  const node = document.querySelector('input[type="password"]')!;
  flushSync(() => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(node, value); node.dispatchEvent(new Event("input", { bubbles: true })); });
}
const tick = () => new Promise((resolve) => setTimeout(resolve, 30));
async function run() {
  const header = document.createElement("div");
  header.className = "chat-page__topbar";
  header.innerHTML = '<button class="chat-page__provider-btn">AI</button><button class="chat-page__name-btn">MAGI</button>';
  document.body.append(header);
  for (const width of [360, 700]) {
    header.style.width = `${width}px`;
    const button = header.querySelector(".chat-page__provider-btn") as HTMLElement;
    const box = button.getBoundingClientRect();
    check(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2) === button, "provider entry is not covered by titlebar drag layer");
    check(getComputedStyle(button).getPropertyValue("-webkit-app-region") === "no-drag", "provider entry is clickable in Electron");
  }
  header.remove();
  for (const [locale, title, save] of [["zh", "模型供应商配置", "保存"], ["en", "Model provider", "Save"], ["ja", "モデルプロバイダー設定", "保存"]]) {
    render(locale);
    check(document.body.textContent?.includes(title), `title ${locale}`);
    check(buttons()[0].textContent === save, `save ${locale}`);
  }
  render("zh");
  select(0, "minimax-cn");
  check(selects()[1].value === "MiniMax-M3", "exact MiniMax ID");
  check(document.querySelectorAll('input[type="text"]').length === 0, "no known URL/model input");
  password("synthetic-browser-key");
  select(0, "custom");
  check(document.querySelectorAll('input[type="text"]').length === 2, "custom inputs");
  check((document.querySelector('input[type="password"]') as HTMLInputElement).value === "", "clear key on vendor change");
  select(0, "minimax-global");
  check(selects()[1].value === "MiniMax-M3", "region default");
  password("synthetic-browser-key");
  const form = document.querySelector("form")!;
  flushSync(() => { form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
  await tick();
  check(testState.calls.length === 1, "double submit guarded before async encryption");
  const fields = testState.calls[0].card_result.fields;
  check(fields.base_url === "", "clear old URL");
  check(fields["provider.api_key"].ciphertext === "synthetic-ciphertext", "encrypt before transport");
  testState.resolve(); await tick();
  check(document.body.textContent?.includes("正在验证"), "transport is not verification");
  check((document.querySelector('input[type="password"]') as HTMLInputElement).value === "", "clear plaintext after transport");
  render("zh", undefined, "failed", false);
  check(document.body.textContent?.includes("验证失败"), "failure visible");
  flushSync(() => buttons().at(-1)!.click()); await tick();
  check(testState.calls.at(-1).card_result.action === "reopen", "retry structured action");
  testState.resolve(); await tick();
  render("en", { provider: "minimax-cn", model: "MinimaxM3" });
  check(selects()[1].value === "MinimaxM3" && buttons()[0].disabled, "unknown legacy value is visible and blocked");
  select(1, "MiniMax-M3");
  check(!buttons()[0].disabled, "corrected selection can save");
  render("en", { provider: "minimax-cn", model: "MiniMax-M3" }, "saved");
  check(document.body.textContent?.includes("Saved"), "saved ack");
  check((document.querySelector('input[type="password"]') as HTMLInputElement).value === "", "saved key never prefilled");
  render("en");
  flushSync(() => buttons()[1].click()); await tick();
  check(testState.calls.at(-1).card_reject.id === card.id, "cancel structured action");
  testState.resolve(); await tick();
  render("en", undefined, "cancelled", false);
  check(buttons().some((button) => button.textContent === "Reopen configuration"), "cancel can reopen");
  document.body.dataset.result = "PASS";
}
void run().catch((error) => { document.body.dataset.result = "FAIL"; document.body.append(String(error)); });

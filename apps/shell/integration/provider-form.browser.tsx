import { RuntimeToggle } from "../web/src/RuntimeToggle";
import { ProviderProfile, ProviderLink } from "../web/src/ProviderProfile";
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
  flushSync(() => root.render(<I18nProvider key={key}><FormCardView message={{ type: "form", text: "", chatId: "test-chat", card: { ...card, values }, receipt: state ? { id: card.id, state, error: state === "failed" ? "connection" : undefined, attempt: testState.calls.at(-1)?.card_result?.attempt } : undefined }} /></I18nProvider>));
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
  check(document.body.textContent?.includes("无法连接供应商"), "transport failure is not reported as a bad key");
  check(!(document.querySelector('input[type="password"]') as HTMLInputElement).disabled, "failed form is directly editable");
  password("synthetic-browser-key");
  flushSync(() => document.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  await tick();
  check(testState.calls.at(-1).card_result.attempt !== testState.calls[0].card_result.attempt, "direct retry has a new attempt id");
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
  flushSync(() => root.render(<I18nProvider><ProviderLink handle="@two.magi" /></I18nProvider>));
  const link = document.querySelector("a")!;
  check(link.getAttribute("href") === "#/magi/%40two.magi/provider", "chat deep link targets the correct MAGI profile");
  check(!document.querySelector("form, input"), "chat configuration prompt contains no credential form");
  let running = false;
  let runtimeCalls: string[] = [];
  let finish!: () => void;
  const invoke = async (method: string, payload?: any) => {
    if (method === "magi.info") return { handle: payload.handle, running, online: running };
    runtimeCalls.push(`${method}:${payload.handle}`);
    await new Promise<void>((resolve) => { finish = resolve; }); running = method === "magi.start";
    return {};
  };
  flushSync(() => root.render(<I18nProvider><RuntimeToggle handle="@one.magi" invoke={invoke} /></I18nProvider>));
  await tick();
  const start = document.querySelector("button")!;
  check(start.textContent === "Start" && start.getAttribute("aria-label")?.includes("@one.magi"), "stopped runtime offers accessible start next to avatar");
  flushSync(() => { start.click(); start.click(); });
  check(runtimeCalls.length === 1 && start.disabled, "start is guarded against duplicate clicks");
  finish(); await tick();
  check(document.querySelector("button")?.textContent === "Stop", "confirmed running runtime offers stop");
  flushSync(() => document.querySelector("button")!.click());
  finish(); await tick();
  check(document.querySelector("button")?.textContent === "Start", "confirmed stopped runtime offers start");
  check(runtimeCalls.every((call) => call.endsWith(":@one.magi")), "runtime commands target the profile MAGI");
  flushSync(() => root.render(<I18nProvider><RuntimeToggle handle="@remote.magi" /></I18nProvider>));
  await tick();
  check(document.querySelector("button")?.disabled && document.body.textContent?.includes("Use the desktop app"), "plain browser does not pretend local runtime control is available");
  flushSync(() => root.render(<I18nProvider><ProviderProfile handle="@two.magi" chatId="two-chat" message={{ type: "form", text: "", chatId: "wrong-chat", card: { ...card, handle: "@one.magi" } }} /></I18nProvider>));
  await tick();
  check(!document.querySelector("form"), "profile never shows another MAGI's credential form");
  check(testState.calls.at(-1).card_result.action === "open", "profile loads directly without a model call");
  testState.resolve(); await tick();
  document.body.dataset.result = "PASS";
}
void run().catch((error) => { document.body.dataset.result = "FAIL"; document.body.append(String(error)); });

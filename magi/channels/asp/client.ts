export type AspEvent = {
  type?: string; event_id?: string; chat_id?: string; payload?: Record<string, unknown>;
  /** Chat events are numbered; control events (nickname, provider) are not. */
  sequence?: number;
  request_id?: string; nickname?: string; provider?: string; api_key?: string; model?: string; base_url?: string;
};

export class AspClient {
  private socket: WebSocket | null = null;
  private stopped = false;
  private listener: Promise<void> | null = null;
  constructor(readonly handle: string, readonly base: string, private readonly token: string) {}

  /** True while a socket is open; the listener reconnects on its own when it drops. */
  get connected(): boolean {
    return this.socket !== null && this.socket.readyState === WebSocket.OPEN;
  }

  async connect(onEvent: (event: AspEvent) => Promise<Record<string, unknown> | void>, onError: (error: unknown) => void = () => {}): Promise<void> {
    if (this.listener) return;
    this.stopped = false;
    let markReady: (() => void) | null = null;
    const ready = new Promise<void>((resolve) => { markReady = resolve; });
    this.listener = this.listen(onEvent, () => markReady?.(), onError);
    await ready;
  }

  private async listen(onEvent: (event: AspEvent) => Promise<Record<string, unknown> | void>, ready: () => void, onError: (error: unknown) => void): Promise<void> {
    while (!this.stopped) {
      try {
        await this.listenOnce(onEvent, ready, onError);
      } catch {
        // The connection state is what matters, and ``health()`` reports it; one log
        // line per reconnect attempt would only be noise.
      }
      if (!this.stopped) await sleep(1_000);
    }
  }

  private async listenOnce(onEvent: (event: AspEvent) => Promise<Record<string, unknown> | void>, ready: () => void, onError: (error: unknown) => void): Promise<void> {
    const url = new URL("/connect", this.base);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(url.href, { headers: { Authorization: `Bearer ${this.token}` } });
    this.socket = socket;
    socket.addEventListener("message", (message: MessageEvent) => {
      void (async () => {
        const event = JSON.parse(String(message.data)) as AspEvent;
        const reply = await onEvent(event);
        if (reply && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(reply));
      })().catch(onError);
    });
    await new Promise<void>((resolve, reject) => {
      let opened = false;
      socket.addEventListener("open", () => { opened = true; ready(); }, { once: true });
      socket.addEventListener("close", () => resolve(), { once: true });
      socket.addEventListener("error", () => { if (!opened) reject(new Error("ASP connection failed")); }, { once: true });
    });
    if (this.socket === socket) this.socket = null;
  }

  close(): void { this.stopped = true; this.socket?.close(); this.socket = null; this.listener = null; }

  join(chatId: string): Promise<void> { return this.post(`/chats/${encodeURIComponent(chatId)}/join`); }
  send(chatId: string, content: string): Promise<void> { return this.post(`/chats/${encodeURIComponent(chatId)}/messages`, { content }); }

  private async post(path: string, payload?: Record<string, unknown>): Promise<void> {
    const response = await fetch(new URL(path, this.base), {
      method: "POST", headers: { Authorization: `Bearer ${this.token}`, "Content-Type": "application/json" },
      body: payload ? JSON.stringify(payload) : undefined,
    });
    if (!response.ok) throw new Error(`ASP HTTP ${response.status}: ${(await response.text()).slice(0, 500)}`);
  }
}
import { setTimeout as sleep } from "node:timers/promises";
import WebSocket from "ws";

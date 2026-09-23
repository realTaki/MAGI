export type AspEvent = { type?: string; session_id?: string; payload?: Record<string, unknown>; request_id?: string; nickname?: string };

export class AspClient {
  private socket: WebSocket | null = null;
  constructor(readonly handle: string, readonly base: string, private readonly token: string) {}

  async connect(onEvent: (event: AspEvent) => Promise<Record<string, unknown> | void>): Promise<void> {
    const url = new URL("/connect", this.base);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    const Socket = WebSocket as unknown as { new (url: string, options: Bun.WebSocketOptions): WebSocket };
    const socket = new Socket(url.href, { headers: { Authorization: `Bearer ${this.token}` } });
    this.socket = socket;
    socket.addEventListener("message", (message) => {
      void (async () => {
        const event = JSON.parse(String(message.data)) as AspEvent;
        const reply = await onEvent(event);
        if (reply && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(reply));
      })().catch((error) => console.error("ASP event:", error));
    });
    await new Promise<void>((resolve, reject) => {
      socket.addEventListener("open", () => resolve(), { once: true });
      socket.addEventListener("error", () => reject(new Error("ASP connection failed")), { once: true });
    });
  }

  close(): void { this.socket?.close(); this.socket = null; }

  join(sessionId: string): Promise<void> { return this.post(`/sessions/${encodeURIComponent(sessionId)}/join`); }
  send(sessionId: string, content: string): Promise<void> { return this.post(`/sessions/${encodeURIComponent(sessionId)}/messages`, { content }); }

  private async post(path: string, payload?: Record<string, unknown>): Promise<void> {
    const response = await fetch(new URL(path, this.base), {
      method: "POST", headers: { Authorization: `Bearer ${this.token}`, "Content-Type": "application/json" },
      body: payload ? JSON.stringify(payload) : undefined,
    });
    if (!response.ok) throw new Error(`ASP HTTP ${response.status}: ${(await response.text()).slice(0, 500)}`);
  }
}

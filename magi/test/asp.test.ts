import { expect, test } from "bun:test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Magi } from "../magi.js";

test("ASP invite enters ChatNotify and reply is delivered to the session", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "asp-"));
  const requests: Array<{ path: string; body: unknown }> = [];
  const replies: unknown[] = [];
  let socket: Bun.ServerWebSocket<unknown> | null = null;
  const server = Bun.serve({
    port: 0,
    fetch(request, host) {
      const url = new URL(request.url);
      if (url.pathname === "/connect") return host.upgrade(request) ? undefined : new Response("upgrade failed", { status: 400 });
      return (async () => {
        const raw = await request.text();
        requests.push({ path: url.pathname, body: raw ? JSON.parse(raw) as unknown : null });
        return Response.json({});
      })();
    },
    websocket: { open(ws) { socket = ws; }, message(_ws, message) { replies.push(JSON.parse(String(message))); } },
  });
  const magi = new Magi("@alice.magi", {
    workspace,
    asp: { base: `http://127.0.0.1:${server.port}`, token: "test-token" },
    client: { async complete() { return { role: "assistant", content: "hello back" } as const; } },
  });
  try {
    magi.start();
    await magi.asp!.connect();
    expect(socket).not.toBeNull();
    socket!.send(JSON.stringify({
      type: "session.invited", event_id: "event-1", session_id: "s1",
      payload: { invitee: "@alice.magi", initial_message: { content: "hello" } },
    }));
    for (let i = 0; i < 200 && (requests.length < 2 || replies.length < 1); i++) await Bun.sleep(10);
    expect(requests).toEqual([
      { path: "/sessions/s1/join", body: null },
      { path: "/sessions/s1/messages", body: { content: "hello back" } },
    ]);
    expect(replies).toEqual([{ type: "session.ack", session_id: "s1", event_id: "event-1" }]);
    const conversation = magi.bus.conversations.forChannel("asp", "s1");
    expect(magi.bus.messages.list(conversation.id).map((message) => message.content)).toEqual(["hello", "hello back"]);
  } finally {
    await magi.stop();
    server.stop(true);
    await rm(workspace, { recursive: true, force: true });
  }
});

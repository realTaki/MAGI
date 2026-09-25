import { expect, test } from "bun:test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Magi } from "../magi.js";
import { MAGI_CONTACT_ID, SYSTEM_CONTACT_ID } from "../bus/index.js";

test("a replayed event is not taken in twice", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "asp-replay-"));
  const acks: string[] = [];
  let completions = 0;
  let socket: Bun.ServerWebSocket<unknown> | null = null;
  const server = Bun.serve({
    port: 0,
    fetch(request, host) {
      const url = new URL(request.url);
      if (url.pathname === "/connect") return host.upgrade(request) ? undefined : new Response("upgrade failed", { status: 400 });
      return Response.json({});
    },
    websocket: {
      open(ws) { socket = ws; },
      message(_ws, message) {
        const event = JSON.parse(String(message)) as { type?: string; event_id?: string };
        if (event.type === "session.ack" && event.event_id) acks.push(event.event_id);
      },
    },
  });
  const magi = new Magi("@eva-000.magi", {
    workspace,
    asp: { base: `http://127.0.0.1:${server.port}`, token: "test-token" },
    client: { async complete() { completions++; return { role: "assistant", content: "heard" } as const; } },
  });
  try {
    await magi.start();
    for (let i = 0; i < 100 && !socket; i++) await Bun.sleep(10);
    const event = {
      type: "session.message", event_id: "dup-1", session_id: "replay", sequence: 7,
      payload: { sender: "user", content: "hello" },
    };
    socket!.send(JSON.stringify(event));
    for (let i = 0; i < 100 && completions < 1; i++) await Bun.sleep(10);

    // ASP replays what it has not seen acknowledged: the same message, sequence and all.
    socket!.send(JSON.stringify(event));
    for (let i = 0; i < 100 && acks.length < 2; i++) await Bun.sleep(10);

    expect(acks).toEqual(["dup-1", "dup-1"]);
    expect(completions).toBe(1);
    const conversation = magi.bus.conversations.forChannel("asp", "replay");
    expect(magi.bus.messages.list(conversation.id).map((message) => message.content)).toEqual(["hello", "heard"]);
  } finally {
    await magi.stop();
    server.stop(true);
    await rm(workspace, { recursive: true, force: true });
  }
});

test("each speaker in a session is a contact of their own", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "asp-speakers-"));
  let socket: Bun.ServerWebSocket<unknown> | null = null;
  const server = Bun.serve({
    port: 0,
    fetch(request, host) {
      const url = new URL(request.url);
      if (url.pathname === "/connect") return host.upgrade(request) ? undefined : new Response("upgrade failed", { status: 400 });
      return Response.json({});
    },
    websocket: { open(ws) { socket = ws; }, message() {} },
  });
  const magi = new Magi("@alice.magi", {
    workspace,
    asp: { base: `http://127.0.0.1:${server.port}`, token: "test-token" },
    client: { async complete() { return { role: "assistant", content: "ok" } as const; } },
  });
  try {
    await magi.start();
    for (let i = 0; i < 100 && !socket; i++) await Bun.sleep(10);
    const conversation = magi.bus.conversations.forChannel("asp", "shared");
    const emit = (id: string, sender: string, content: string) => socket!.send(JSON.stringify({
      type: "session.message", event_id: id, session_id: "shared", payload: { sender, content },
    }));
    emit("m1", "user", "morning");
    emit("m2", "@eva-001.magi", "morning yourself");

    const senders = () => new Set(magi.bus.messages.list(conversation.id).map((message) => message.contact_id));
    for (let i = 0; i < 100 && senders().size < 2; i++) await Bun.sleep(10);
    expect(senders()).toContain(SYSTEM_CONTACT_ID);
    const other = magi.bus.contacts.list().find((contact) => contact.asp_handle === "@eva-001.magi");
    expect(other).toMatchObject({ name: "@eva-001.magi", role: "magi" });
    expect(senders()).toContain(other!.id);
    // Both of them are members of the conversation, in the order they were heard.
    expect(magi.bus.conversationMembers.list(conversation.id).map((contact) => contact.id))
      .toEqual([SYSTEM_CONTACT_ID, other!.id]);
  } finally {
    await magi.stop();
    server.stop(true);
    await rm(workspace, { recursive: true, force: true });
  }
});

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
      if (request.method === "GET" && url.pathname === "/sessions/s1") return Response.json({ kind: "bot" });
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
    await magi.start();
    // The ASP worker connects in the background; wait until its socket is up.
    for (let i = 0; i < 200 && !socket; i++) await Bun.sleep(10);
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

test("mentions decide who answers, and everything said is kept", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "asp-group-"));
  const sent: string[] = [];
  const acks: string[] = [];
  let completions = 0;
  let socket: Bun.ServerWebSocket<unknown> | null = null;
  const server = Bun.serve({
    port: 0,
    fetch(request, host) {
      const url = new URL(request.url);
      if (url.pathname === "/connect") return host.upgrade(request) ? undefined : new Response("upgrade failed", { status: 400 });
      if (request.method === "POST" && url.pathname === "/sessions/group/messages") {
        return (async () => { sent.push(String((await request.json() as { content: string }).content)); return Response.json({}); })();
      }
      return Response.json({});
    },
    websocket: {
      open(ws) { socket = ws; },
      message(_ws, message) {
        const event = JSON.parse(String(message)) as { type?: string; event_id?: string };
        if (event.type === "session.ack" && event.event_id) acks.push(event.event_id);
      },
    },
  });
  const magi = new Magi("@eva-000.magi", {
    workspace,
    asp: { base: `http://127.0.0.1:${server.port}`, token: "test-token" },
    client: { async complete() { completions++; return { role: "assistant", content: "heard" } as const; } },
  });
  try {
    await magi.start();
    // The ASP worker connects in the background; wait until its socket is up.
    for (let i = 0; i < 200 && !socket; i++) await Bun.sleep(10);
    expect(socket).not.toBeNull();
    const emit = (id: string, sender: string, content: string, mentions?: string[]) => socket!.send(JSON.stringify({
      type: "session.message", event_id: id, session_id: "group",
      payload: { sender, content, ...(mentions === undefined ? {} : { mentions }) },
    }));
    // Nobody named: for whoever can help, so this MAGI answers.
    emit("m1", "@eva-001.magi", "I can hear you");
    for (let i = 0; i < 100 && completions < 1; i++) await Bun.sleep(10);
    // Named someone else: recorded, but not this MAGI's turn.
    emit("m2", "@eva-002.magi", "Me too", ["@eva-009.magi"]);
    // Named this MAGI: its turn.
    emit("m3", "user", "@eva-000.magi can you hear me?", ["@eva-000.magi"]);
    for (let i = 0; i < 200 && (completions < 2 || sent.length < 2); i++) await Bun.sleep(10);
    // Another agent naming someone else again: recorded only.
    emit("m4", "@eva-001.magi", "@eva-002.magi, you there?", ["@eva-002.magi"]);
    for (let i = 0; i < 200 && acks.length < 4; i++) await Bun.sleep(10);

    expect(acks.sort()).toEqual(["m1", "m2", "m3", "m4"]);
    expect(completions).toBe(2);
    expect(sent).toEqual(["heard", "heard"]);

    const conversation = magi.bus.conversations.forChannel("asp", "group");
    const stored = magi.bus.messages.list(conversation.id);
    const contents = stored.map((message) => message.content);
    // Nothing is dropped: what the others said stays in the history.
    expect(contents).toContain("I can hear you");
    expect(contents).toContain("Me too");
    expect(contents).toContain("@eva-002.magi, you there?");
    expect(contents.filter((content) => content === "heard")).toHaveLength(2);

    const handle = (asp_handle: string) => magi.bus.contacts.list().find((contact) => contact.asp_handle === asp_handle);
    expect(new Set(stored.map((message) => message.contact_id))).toEqual(new Set([
      SYSTEM_CONTACT_ID, MAGI_CONTACT_ID, handle("@eva-001.magi")!.id, handle("@eva-002.magi")!.id,
    ]));
    expect(new Set(magi.bus.conversationMembers.list(conversation.id).map((contact) => contact.asp_handle)))
      .toEqual(new Set(["@eva-001.magi", "@eva-002.magi", "user"]));
  } finally {
    await magi.stop();
    server.stop(true);
    await rm(workspace, { recursive: true, force: true });
  }
});

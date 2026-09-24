import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";

import { isRecord, request, tempRoot, withApp } from "./helpers.ts";

test("relay survives restart and requires exact recipient acks", async (t) => {
  const databasePath = path.join(tempRoot(t), "asp.sqlite");
  const options = {
    databasePath,
    aspSeed: { "@second.magi": "second-token" },
  };
  let sessionId = "";
  let firstEventId = "";
  await withApp(options, async (app) => {
    const token = operatorToken(await request(app, "GET", "/operator"));
    const conversation = record(
      (await request(app, "POST", "/conversations", { token, body: { kind: "group" } })).data,
    );
    sessionId = String(conversation.conversation_id);
    assert.equal(
      (
        await request(app, "POST", `/conversations/${sessionId}/members`, {
          token,
          body: { handle: "@second.magi" },
        })
      ).status,
      200,
    );
    assert.equal((await request(app, "POST", `/sessions/${sessionId}/join`, { token: "second-token" })).status, 200);
    for (const content of ["one", "two"]) {
      assert.equal(
        (await request(app, "POST", `/sessions/${sessionId}/messages`, { token, body: { content } })).status,
        201,
      );
    }
    const events = eventsOf(await request(app, "GET", `/sessions/${sessionId}/events`, { token }));
    const messages = events.filter((event) => event.type === "session.message");
    assert.deepEqual(
      messages.map((event) => record(event.payload).content),
      ["one", "two"],
    );
    const first = messages[0];
    const later = messages[1];
    if (first === undefined || later === undefined) {
      throw new Error("expected two messages");
    }
    firstEventId = String(first.event_id);
    for (const bearer of [token, "second-token"]) {
      assert.equal(
        (
          await request(app, "POST", `/sessions/${sessionId}/events/ack`, {
            token: bearer,
            body: { event_ids: [later.event_id] },
          })
        ).status,
        200,
      );
    }
    const remaining = eventsOf(await request(app, "GET", `/sessions/${sessionId}/events`, { token })).filter(
      (event) => event.type === "session.message",
    );
    assert.deepEqual(
      remaining.map((event) => record(event.payload).content),
      ["one"],
    );
  });

  await withApp(options, async (app) => {
    const token = operatorToken(await request(app, "GET", "/operator"));
    const conversations = record((await request(app, "GET", "/conversations", { token })).data).conversations;
    assert.ok(Array.isArray(conversations));
    assert.equal(record(conversations[0]).conversation_id, sessionId);
    assert.ok(eventsOf(await request(app, "GET", `/sessions/${sessionId}/events`, { token })).length > 0);
    const ack = `/sessions/${sessionId}/events/ack`;
    assert.equal(
      (await request(app, "POST", ack, { token, body: { event_ids: [firstEventId] } })).status,
      200,
    );
    let remaining = eventsOf(await request(app, "GET", `/sessions/${sessionId}/events`, { token }));
    assert.equal(
      remaining.some((event) => event.event_id === firstEventId),
      true,
    );
    assert.equal(
      (await request(app, "POST", ack, { token: "second-token", body: { event_ids: [firstEventId] } })).status,
      200,
    );
    remaining = eventsOf(await request(app, "GET", `/sessions/${sessionId}/events`, { token }));
    assert.equal(
      remaining.every((event) => event.type !== "session.message"),
      true,
    );
  });
});

test("a registered magi outlives an asp restart", async (t) => {
  const databasePath = path.join(tempRoot(t), "asp.sqlite");
  let handle = "";
  await withApp({ databasePath }, async (app) => {
    const token = operatorToken(await request(app, "GET", "/operator"));
    const response = await request(app, "POST", "/conversations", { token, body: { kind: "bot" } });
    assert.equal(response.status, 201);
    const agents = record(response.data).agents;
    if (!Array.isArray(agents) || typeof agents[0] !== "string") {
      throw new Error("expected a registered handle");
    }
    handle = agents[0];
  });
  // Restarting ASP must not lose the agent: the desktop app is what runs it,
  // and it finds the same record (and token) when it reconciles.
  await withApp({ databasePath }, async (app) => {
    const token = operatorToken(await request(app, "GET", "/operator"));
    const roster = recordList(
      record((await request(app, "GET", "/agents", { token })).data).agents,
    ).filter((agent) => agent.managed === true);
    assert.deepEqual(roster.map((agent) => agent.handle), [handle]);
    assert.equal(roster[0]?.managed, true);
    assert.equal(typeof roster[0]?.token, "string");
  });
});

function operatorToken(response: { data: unknown }): string {
  if (!isRecord(response.data) || typeof response.data.token !== "string") {
    throw new Error("operator token missing");
  }
  return response.data.token;
}

function record(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) {
    throw new Error("expected an object");
  }
  return value;
}

function recordList(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) {
    throw new Error("expected a list");
  }
  return value.map((item) => record(item));
}

function eventsOf(response: { data: unknown }): Record<string, unknown>[] {
  const events = record(response.data).events;
  if (!Array.isArray(events)) {
    throw new Error("expected events");
  }
  return events.map((event) => record(event));
}

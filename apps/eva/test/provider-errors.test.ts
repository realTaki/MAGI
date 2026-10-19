import test from "node:test";
import assert from "node:assert/strict";
import { PiAiClient } from "@magi/providers/client.js";
import { providerFailureCode } from "@magi/bus/jobs/providerFailure.js";

export function probeStream(stop = "end_turn") {
  return new Response([
    ["message_start", { type: "message_start", message: { id: "mock", type: "message", role: "assistant", model: "MiniMax-M3", content: [], stop_reason: null, usage: { input_tokens: 1, output_tokens: 0 } } }],
    ["content_block_start", { type: "content_block_start", index: 0, content_block: { type: "text", text: "" } }],
    ["content_block_delta", { type: "content_block_delta", index: 0, delta: { type: "text_delta", text: "OK" } }],
    ["content_block_stop", { type: "content_block_stop", index: 0 }],
    ["message_delta", { type: "message_delta", delta: { stop_reason: stop }, usage: { output_tokens: 2 } }],
    ["message_stop", { type: "message_stop" }],
  ].map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join(""), { headers: { "Content-Type": "text/event-stream" } });
}
const settings = { provider: "minimax-cn", model: "MiniMax-M3", api_key: "synthetic-never-print" };
test("provider diagnostics distinguish transport, rejected credentials, unsupported model and quota without secrets", async () => {
  for (const [status, body, code] of [
    [401, { error: { type: "authentication_error", message: settings.api_key } }, "authentication"],
    [403, { error: { type: "permission_error" } }, "authentication"],
    [400, { error: { type: "invalid_request_error", message: `model is not supported ${settings.api_key}` } }, "model"],
    [404, { error: { code: "model_not_found" } }, "model"],
    [404, { error: { message: "unknown endpoint" } }, "configuration"],
    [429, {}, "rate_limit"], [503, {}, "server"],
  ] as const) {
    let calls = 0;
    const client = new PiAiClient(settings, (async () => { calls++; return Response.json(body, { status }); }) as typeof fetch);
    await assert.rejects(client.verify(settings), (error: Error) => providerFailureCode(error) === code && !error.message.includes(settings.api_key));
    assert.equal(calls, 1, "configuration validation never silently retries a paid request");
  }
  const offline = new PiAiClient(settings, (async () => { throw new TypeError(`fetch failed ${settings.api_key}`); }) as typeof fetch);
  await assert.rejects(offline.verify(settings), (error: Error) => providerFailureCode(error) === "connection" && !error.message.includes(settings.api_key));
  let fetched = false;
  const unknown = new PiAiClient(settings, (async () => { fetched = true; return probeStream(); }) as typeof fetch);
  await assert.rejects(unknown.verify({ ...settings, model: "MinimaxM3" }), (error: Error) => providerFailureCode(error) === "model");
  assert.equal(fetched, false);
});
test("short verification accepts a completed length-limited probe, while ordinary completions reject truncation", async () => {
  const client = new PiAiClient(settings, (async () => probeStream("max_tokens")) as typeof fetch);
  await client.verify(settings);
  await assert.rejects(client.complete({ messages: [{ role: "user", content: "Hi" }], tools: [] }));
  const fresh = new PiAiClient({});
  await assert.rejects(fresh.complete({ messages: [{ role: "user", content: "Hi" }], tools: [] }), (error: Error) => providerFailureCode(error) === "unconfigured");
});

test("transport diagnostics use bounded structured causes and never expose raw errors", async () => {
  const secret = "synthetic-never-print";
  const wrapped = (code: string) => new TypeError(`fetch failed https://${secret}.invalid`, {
    cause: Object.assign(new Error(`Authorization: Bearer ${secret}`), { code }),
  });
  const cycle = Object.assign(new Error(secret), { cause: undefined as unknown });
  cycle.cause = cycle;
  const cases = [
    [wrapped("ENOTFOUND"), "dns"], [wrapped("EAI_AGAIN"), "dns"],
    [wrapped("ECONNREFUSED"), "connection_refused"],
    [wrapped("CERT_HAS_EXPIRED"), "tls"], [wrapped("ERR_TLS_CERT_ALTNAME_INVALID"), "tls"],
    [wrapped("UNABLE_TO_VERIFY_LEAF_SIGNATURE"), "tls"],
    [wrapped("ETIMEDOUT"), "timeout"], [wrapped("UND_ERR_CONNECT_TIMEOUT"), "timeout"],
    [wrapped("UND_ERR_HEADERS_TIMEOUT"), "timeout"], [wrapped("UND_ERR_BODY_TIMEOUT"), "timeout"],
    [new DOMException(secret, "AbortError"), "timeout"],
    [new DOMException(secret, "TimeoutError"), "timeout"],
    [new TypeError(secret, { cause: new AggregateError([wrapped("ECONNREFUSED"), wrapped("ECONNREFUSED")]) }), "connection_refused"],
    [new AggregateError([wrapped("ENOTFOUND"), wrapped("ECONNREFUSED")]), "connection"],
    [wrapped("ECONNRESET"), "connection"], [wrapped(secret), "connection"],
    [wrapped("constructor"), "connection"], [wrapped("__proto__"), "connection"],
    [new Error(`ENOTFOUND timeout ${secret}`), "connection"], [cycle, "connection"],
  ] as const;
  for (const [failure, expected] of cases) {
    let calls = 0;
    const client = new PiAiClient(settings, (async () => { calls++; throw failure; }) as typeof fetch);
    await assert.rejects(client.verify(settings), (error: Error) => {
      assert.equal(providerFailureCode(error), expected);
      assert.ok(!error.message.includes(secret));
      assert.ok(!error.message.includes("Authorization"));
      assert.ok(!error.message.includes("https://"));
      return true;
    });
    assert.equal(calls, 1);
  }
});

test("HTTP server failures remain distinct from transport failures and gateway timeouts", async () => {
  for (const status of [500, 502, 503, 504, 599]) {
    const client = new PiAiClient(settings, (async () => new Response(settings.api_key, { status })) as typeof fetch);
    await assert.rejects(client.verify(settings), (error: Error) =>
      providerFailureCode(error) === (status === 504 ? "timeout" : "server") && !error.message.includes(settings.api_key));
  }
});

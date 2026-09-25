import { expect, test } from "bun:test";
import { PiAiClient } from "../providers/client.js";

test("MiniMax CN and Global use separate Anthropic-compatible endpoints", async () => {
  const events = [
    ["message_start", { type: "message_start", message: { id: "msg_1", type: "message", role: "assistant", model: "MiniMax-M3", content: [], stop_reason: null, usage: { input_tokens: 1, output_tokens: 0 } } }],
    ["content_block_start", { type: "content_block_start", index: 0, content_block: { type: "text", text: "" } }],
    ["content_block_delta", { type: "content_block_delta", index: 0, delta: { type: "text_delta", text: "OK" } }],
    ["content_block_stop", { type: "content_block_stop", index: 0 }],
    ["message_delta", { type: "message_delta", delta: { stop_reason: "end_turn", stop_sequence: null }, usage: { output_tokens: 1 } }],
    ["message_stop", { type: "message_stop" }],
  ];

  for (const [provider, endpoint] of [
    ["minimax-cn", "https://api.minimaxi.com/anthropic/v1/messages"],
    ["minimax-global", "https://api.minimax.io/anthropic/v1/messages"],
  ]) {
    let requested = "";
    let apiKey = "";
    const client = new PiAiClient({ provider, model: "MiniMax-M3", api_key: "sk-test" },
      (async (input, init) => {
        requested = String(input);
        apiKey = new Headers(init?.headers).get("x-api-key") ?? "";
        return new Response(events.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join(""),
          { headers: { "Content-Type": "text/event-stream" } });
      }) as typeof fetch);
    const message = await client.complete({ messages: [{ role: "user", content: "hello" }], tools: [] });
    expect(requested).toStartWith(endpoint);
    expect(apiKey).toBe("sk-test");
    expect(message.content).toBe("OK");
  }
});

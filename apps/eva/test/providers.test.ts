import { expect, test, sleep } from "./test.js";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Magi } from "../eva.js";

test("provider_settings verifies a change before it becomes active", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "magi-providers-"));
  const configured: Array<Record<string, unknown>> = [];
  const magi = new Magi("@providers.magi", {
    workspace,
    client: {
      async complete() { return { role: "assistant", content: "unused" }; },
      async verify(settings) { if (settings.api_key === "bad-secret") throw new Error("invalid key bad-secret"); },
      configure(settings) { configured.push(settings); },
    },
  });
  const board = () => magi.bus.board("RunToolJob");
  const call = (args: Record<string, unknown>): number =>
    board().publish({ call: { tool_call_id: "call-1", name: "provider_settings", arguments: args } }, "test");
  const settled = async (id: number) => {
    for (let i = 0; i < 200; i++) {
      const result = board().result(id);
      if (result) return result;
      await sleep(10);
    }
    throw new Error("provider_settings did not finish");
  };
  try {
    await magi.start();
    const wanted = { provider: "custom", model: "gpt-test", base_url: "https://example.com/v1" };

    const bad = await settled(call({ action: "update", ...wanted, api_key: "bad-secret" }));
    expect(bad).toMatchObject({ status: "failed", error: "invalid key [redacted]" });
    expect(magi.bus.settings.get("provider.api_key")).toBeNull();
    expect(configured).toHaveLength(0);

    const good = await settled(call({ action: "update", ...wanted, api_key: "good-secret" }));
    expect(good).toMatchObject({ status: "completed" });
    expect(good.output?.content).toContain("updated");
    expect(magi.bus.settings.get("provider.name")).toBe("custom");
    expect(magi.bus.settings.get("provider.model")).toBe("gpt-test");
    expect(magi.bus.settings.get("provider.base_url")).toBe("https://example.com/v1");
    expect(magi.bus.settings.get("provider.api_key")).toBe("good-secret");
    expect(configured).toHaveLength(1);

    // `show` reports what is active, never the key itself.
    const shown = await settled(call({ action: "show" }));
    expect(shown.output?.content).toContain('"api_key_set":true');
    expect(shown.output?.content).not.toContain("good-secret");
  } finally {
    await magi.stop();
    await rm(workspace, { recursive: true, force: true });
  }
});

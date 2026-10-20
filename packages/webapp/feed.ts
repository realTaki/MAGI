import { SHELL_MAGI_HANDLE, waitForJob, type Bus } from "@magi/bus";

/** Local owner projection. It never creates/controls a worker or calls one directly. */
export function feedApi(bus: Bus) {
  function available() {
    if (bus.handle !== SHELL_MAGI_HANDLE || !bus.tools.get("configure_feed")) throw new Error("Feed is not running on this Shell EVA");
  }
  async function command(name: string, args: Record<string, unknown>) {
    available();
    const board = bus.board("RunToolJob");
    const chat = bus.chats.forChannel("shell-owner", "owner");
    const id = board.publish({ call: { name, arguments: args, tool_call_id: `feed-owner-${Date.now()}` }, chat_id: chat.id }, "webapp-feed");
    try {
      const result = await waitForJob(() => board.result(id), 15_000, "Feed change");
      if (result.status === "failed") throw new Error(result.error ?? "Feed change failed");
      return JSON.parse(result.output?.content ?? "{}");
    } catch (error) { board.failPending(id, "Owner request timed out before execution"); throw error; }
  }
  return {
    "feed.state": () => {
      if (bus.handle !== SHELL_MAGI_HANDLE) throw new Error("Feed belongs only to Shell EVA");
      const running = Boolean(bus.tools.get("configure_feed"));
      const sources = bus.tools.catalog().filter(tool => bus.tools.get(tool.name)?.researchSource?.readOnlyHint === true);
      const selected: string[] = JSON.parse(bus.settings.get("feed.sources") ?? "[]");
      const enabled = bus.settings.get("feed.enabled") === "true";
      const provider = Boolean(bus.settings.get("provider.name") && bus.secrets.get("provider.api_key"));
      const used = bus.settings.get("feed.research_day") === new Date().toISOString().slice(0, 10) ? Number(bus.settings.get("feed.research_count") ?? "0") : 0;
      const budget = Number(bus.settings.get("feed.daily_budget") ?? "3");
      return { enabled, used_today: used, daily_budget: Number(bus.settings.get("feed.daily_budget") ?? "3"), prompt: bus.prompts.get("feed/AGENT") ?? "", selected, sources,
        status: !running ? "paused" : !enabled ? "disabled" : !selected.some(name => sources.some(source => source.name === name)) ? "sourceNeeded" : !provider ? "providerNeeded" : used >= budget ? "budgetExhausted" : "ready",
        last_attempt: bus.settings.get("feed.last_attempt"), last_error: bus.settings.get("feed.last_error"),
        items: bus.feed.list(true).map(item => ({ ...item, sources: item.sources.map(source => ({ ...source, kind: bus.feed.evidence(source.evidence_id)?.kind ?? "search-snippet" })) })),
      };
    },
    "feed.configure": (args: Record<string, unknown>) => command("configure_feed", args),
    "feed.feedback": (args: Record<string, unknown>) => command("set_feed_feedback", args),
  };
}

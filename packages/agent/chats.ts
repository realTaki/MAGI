import { MAGI_CONTACT_ID, SYSTEM_CONTACT_ID, messageDelivery, type Bus, type LLMMessage } from "@magi/bus";
import { SYSTEM_PROMPT } from "./prompt_defaults.js";
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const COMPACT_KEEP_RECENT = 20;
const COMPACT_CONTEXT_WINDOW = 200_000;
/** Told to the model, not enforced: a long task should stop and ask, not be cut off. */
const SUGGESTED_STEPS = 20;
/** How the model ends a turn in a room without posting anything. */
const NO_REPLY = "NO_REPLY";

export class Chat {
  constructor(
    private readonly bus: Bus,
    readonly chat_id: number,
  ) {}

  async run(jobId: number): Promise<void> {
    const chatBoard = this.bus.board("MessageDeliveryJob");
    try {
      if (!this.bus.agentTurns.has(jobId)) await this.initialize(jobId);
      await this.execute(jobId);
    } catch (error) {
      // What went wrong is said in the chat itself: the Job result is for
      // whoever published the turn, not for whoever is waiting for an answer.
      const message = error instanceof Error ? error.message : String(error);
      messageDelivery.send(this.bus, this.chat_id, message, MAGI_CONTACT_ID, "agent");
      chatBoard.submit("agent", jobId, { error: message });
    } finally {
      // Keep the cold append-only trace, but release this process's materialized context.
      this.bus.agentTurns.evict(jobId);
    }
  }

  private async initialize(jobId: number): Promise<void> {
      const record = this.bus.chats.get(this.chat_id);
      if (!record) throw new Error("chat does not exist");
      const agentPrompt = this.bus.prompts.get("agent/AGENT") ?? "You are a helpful assistant.";
      // Blocks the running modules answer for: the agent renders them without knowing
      // which worker is behind one, or whether any worker is behind it at all.
      const contributed: ReadonlyArray<readonly [string, string]> = this.bus.prompts.sections(this.chat_id)
        .map((section): readonly [string, string] => [section.title, section.body]);
      const beforeSummary = this.context([
        ...contributed,
        ["Chat instruction", record.instruction],
        ["Chat info", record.info],
      ]);
      const chat = this.context([
        ["Chat", `chat_id: ${this.chat_id}\nchannel: ${record.channel}\ndelivery_address: ${record.delivery_address}\ntopic: ${record.topic}\nhome_chat_id: ${messageDelivery.homeChat(this.bus) ?? "none"}\nMAGI_CONTACT_ID: ${MAGI_CONTACT_ID}\nSYSTEM_CONTACT_ID: ${SYSTEM_CONTACT_ID}`],
      ]);
      // A turn receives one fixed catalog.  A worker starting midway through a turn
      // becomes available on the next turn instead of changing the model's contract.
      const tools = this.bus.tools.catalog();
      const summary = await this.compact(jobId, record.summary, [agentPrompt, SYSTEM_PROMPT, beforeSummary, chat].filter(Boolean).join("\n\n"), tools);
      const system = [agentPrompt, SYSTEM_PROMPT, beforeSummary, this.context([["Prior chat summary", summary]]), chat].filter(Boolean).join("\n\n");
      // Message rows are already LLM messages: roles and the user identity envelope are
      // assigned when a message is recorded, not rebuilt for every agent turn.
      const history: LLMMessage[] = this.bus.messages.list(this.chat_id, COMPACT_KEEP_RECENT)
        .map(({ llm_role: role, content }) => ({ role, content }));
      // Keep the cacheable instruction/context prefix byte-for-byte stable through a
      // tool loop.  Step metadata is a separate trailing system message.
      this.bus.agentTurns.ensure(jobId, { messages: [{ role: "system", content: system }, { role: "system", content: "" }, ...history], tools });
  }

  private async execute(jobId: number): Promise<void> {
      const chatBoard = this.bus.board("MessageDeliveryJob");
      const { messages } = this.bus.agentTurns.get(jobId);
      // No step limit is enforced: the model is told which step it is on and that it
      // should stop and ask the user before going much past the suggested number.
      for (let step = 1; ; step++) {
        messages[1] = { role: "system", content: this.section("Turn", `step: ${step}\nsuggested maximum: ${SUGGESTED_STEPS}\nStop and ask the user whether to continue once you reach the suggested maximum without finishing.`) };
        const llmId = this.bus.board("CallLLMJob").publish({ turn_id: jobId }, "agent");
        const llm = await this.waitFor("CallLLMJob", llmId, 300_000);
        if (llm.status === "failed" || llm.output?.turn_id !== jobId) throw new Error(llm.error ?? "LLM failed");
        const response = this.bus.agentTurns.assistant(jobId, llmId);
        if (!response.tool_calls?.length) {
          const reply = (response.content ?? "").trim();
          // Saying nothing is a real answer in a room with several people in it, so the
          // prompt lets the model end the turn with NO_REPLY instead of posting.
          if (reply.toUpperCase() !== NO_REPLY) {
            // A Notify is published and not awaited: a channel that cannot deliver reports
            // its own trouble, and there is nothing the agent could do about it here.
            messageDelivery.send(this.bus, this.chat_id, reply || "处理完毕。", MAGI_CONTACT_ID, "agent");
          }
          chatBoard.submit("agent", jobId, { output: {} });
          return;
        }
        if (response.content) messageDelivery.send(this.bus, this.chat_id, response.content, MAGI_CONTACT_ID, "agent");
        // A name the catalog does not have is answered here: no worker would claim its job.
        const calls = response.tool_calls.map((call) => this.bus.tools.get(call.name)
          ? { call, jobId: this.bus.board("RunToolJob").publish({ call }, "agent") as number | null }
          : { call, jobId: null });
        for (const pending of calls) {
          if (pending.jobId === null) {
            this.bus.agentTurns.appendToolResult(jobId, { role: "tool", tool_call_id: pending.call.tool_call_id, tool_name: pending.call.name, content: `unknown tool ${pending.call.name}`, is_error: true });
            continue;
          }
          const result = await this.waitFor("RunToolJob", pending.jobId, 120_000);
          this.bus.agentTurns.appendToolResult(jobId, {
            role: "tool", tool_call_id: pending.call.tool_call_id, tool_name: pending.call.name,
            content: result.status === "failed" ? result.error ?? "tool failed" : result.output?.content ?? "",
            is_error: result.status === "failed",
          });
        }
      }
  }

  private async compact(turnId: number, previousSummary: string, staticContext: string, tools: unknown): Promise<string> {
    const active = this.bus.messages.list(this.chat_id, 10_000);
    // We cannot use one tokenizer for all providers, but every stable input part
    // must count.  The previous estimate considered only stored chat rows, which
    // understated large prompt blocks and tool catalogs.
    const estimatedTokens = [
      ...active.map((message) => message.content),
      previousSummary,
      staticContext,
      JSON.stringify(tools),
      this.section("Turn", `step: 1\nsuggested maximum: ${SUGGESTED_STEPS}`),
    ].reduce((sum, text) => sum + Math.max(1, Math.ceil(text.length / 4)), 0);
    const configuredWindow = Number(this.bus.settings.get("provider.context_window"));
    const contextWindow = Number.isFinite(configuredWindow) && configuredWindow > 0 ? configuredWindow : COMPACT_CONTEXT_WINDOW;
    if (estimatedTokens <= Math.floor(contextWindow / 2)) return previousSummary;
    const old = active.slice(0, -COMPACT_KEEP_RECENT);
    if (!old.length) return previousSummary;
    const content = old.map((message) => `[${message.llm_role}]\n${message.content}`).join("\n\n");
    this.bus.agentTurns.ensure(turnId, { messages: [
        { role: "system", content: this.bus.prompts.get("agent/compaction") ?? "Summarize the chat." },
        { role: "user", content: `${previousSummary ? `Previous summary:\n${previousSummary}\n\n` : ""}Transcript:\n${content}\n\n请仅总结上面的对话历史，并遵循 system 指令。` },
      ], tools: [] });
    const id = this.bus.board("CallLLMJob").publish({ turn_id: turnId }, "agent");
    const result = await this.waitFor("CallLLMJob", id, 300_000);
    const response = result.status === "completed" && result.output?.turn_id === turnId ? this.bus.agentTurns.assistant(turnId, id) : null;
    const summary = response?.content.trim() ?? "";
    // The compaction result is durable in ChatBook; the turn's persistent cache starts
    // fresh with the real interaction context rather than retaining this helper call.
    this.bus.agentTurns.reset(turnId);
    if (!summary || response?.tool_calls?.length) return previousSummary;
    this.bus.chats.updateSummary(this.chat_id, summary);
    this.bus.messages.archiveBefore(this.chat_id, old.at(-1)!.id);
    return summary;
  }

  /** Render durable runtime data in the same ordered, readable system-context block. */
  private context(sections: ReadonlyArray<readonly [title: string, body: string]>): string {
    return sections.filter(([, body]) => Boolean(body)).map(([title, body]) => this.section(title, body)).join("\n\n");
  }

  private section(title: string, body: string): string {
    return `## ${title}\n${body}`;
  }

  private async waitFor<K extends "CallLLMJob" | "RunToolJob">(type: K, id: number, timeoutMs: number) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const result = this.bus.board(type).result(id);
      if (result) return result;
      await sleep(10);
    }
    throw new Error(`${type} timed out`);
  }
}

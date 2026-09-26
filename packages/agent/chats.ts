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
      const record = this.bus.chats.get(this.chat_id);
      if (!record) throw new Error("chat does not exist");
      const agentPrompt = this.bus.prompts.get("agent/AGENT") ?? "You are a helpful assistant.";
      const summary = await this.compact(record.summary);
      // Blocks the running modules answer for: the agent renders them without knowing
      // which worker is behind one, or whether any worker is behind it at all.
      const contributed: ReadonlyArray<readonly [string, string]> = this.bus.prompts.sections(this.chat_id)
        .map((section): readonly [string, string] => [section.title, section.body]);
      const chatContext = this.context([
        ...contributed,
        ["Chat instruction", record.instruction],
        ["Chat info", record.info],
        ["Prior chat summary", summary],
        ["Chat", `chat_id: ${this.chat_id}\nchannel: ${record.channel}\ndelivery_address: ${record.delivery_address}\ntopic: ${record.topic}\nhome_chat_id: ${messageDelivery.homeChat(this.bus) ?? "none"}\nMAGI_CONTACT_ID: ${MAGI_CONTACT_ID}\nSYSTEM_CONTACT_ID: ${SYSTEM_CONTACT_ID}`],
      ]);
      const system = [agentPrompt, SYSTEM_PROMPT, chatContext].filter(Boolean).join("\n\n");
      // Message rows are already LLM messages: roles and the user identity envelope are
      // assigned when a message is recorded, not rebuilt for every agent turn.
      const history: LLMMessage[] = this.bus.messages.list(this.chat_id, COMPACT_KEEP_RECENT)
        .map(({ llm_role: role, content }) => ({ role, content }));
      const messages: LLMMessage[] = [{ role: "system", content: system }, ...history];
      // No step limit is enforced: the model is told which step it is on and that it
      // should stop and ask the user before going much past the suggested number.
      for (let step = 1; ; step++) {
        messages[0] = { role: "system", content: `${system}\n\n${this.section("Turn", `step: ${step}\nsuggested maximum: ${SUGGESTED_STEPS}\nStop and ask the user whether to continue once you reach the suggested maximum without finishing.`)}` };
        const llmId = this.bus.board("CallLLMJob").publish({ messages, tools: this.bus.tools.catalog() }, "agent");
        const llm = await this.waitFor("CallLLMJob", llmId, 300_000);
        if (llm.status === "failed" || !llm.output?.message) throw new Error(llm.error ?? "LLM failed");
        const response = llm.output.message;
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
        messages.push(response);
        // A name the catalog does not have is answered here: no worker would claim its job.
        const calls = response.tool_calls.map((call) => this.bus.tools.get(call.name)
          ? { call, jobId: this.bus.board("RunToolJob").publish({ call }, "agent") as number | null }
          : { call, jobId: null });
        for (const pending of calls) {
          if (pending.jobId === null) {
            messages.push({ role: "tool", tool_call_id: pending.call.tool_call_id, tool_name: pending.call.name, content: `unknown tool ${pending.call.name}`, is_error: true });
            continue;
          }
          const result = await this.waitFor("RunToolJob", pending.jobId, 120_000);
          messages.push({
            role: "tool", tool_call_id: pending.call.tool_call_id, tool_name: pending.call.name,
            content: result.status === "failed" ? result.error ?? "tool failed" : result.output?.content ?? "",
            is_error: result.status === "failed",
          });
        }
      }
    } catch (error) {
      // What went wrong is said in the chat itself: the Job result is for
      // whoever published the turn, not for whoever is waiting for an answer.
      const message = error instanceof Error ? error.message : String(error);
      messageDelivery.send(this.bus, this.chat_id, message, MAGI_CONTACT_ID, "agent");
      chatBoard.submit("agent", jobId, { error: message });
    }
  }

  private async compact(previousSummary: string): Promise<string> {
    const active = this.bus.messages.list(this.chat_id, 10_000);
    const estimatedTokens = active.reduce((sum, message) => sum + Math.max(1, Math.ceil(message.content.length / 4)), Math.max(0, Math.ceil(previousSummary.length / 4)));
    const configuredWindow = Number(this.bus.settings.get("provider.context_window"));
    const contextWindow = Number.isFinite(configuredWindow) && configuredWindow > 0 ? configuredWindow : COMPACT_CONTEXT_WINDOW;
    if (estimatedTokens <= Math.floor(contextWindow / 2)) return previousSummary;
    const old = active.slice(0, -COMPACT_KEEP_RECENT);
    if (!old.length) return previousSummary;
    const content = old.map((message) => `[${message.llm_role}]\n${message.content}`).join("\n\n");
    const id = this.bus.board("CallLLMJob").publish({
      messages: [
        { role: "system", content: this.bus.prompts.get("agent/compaction") ?? "Summarize the chat." },
        { role: "user", content: `${previousSummary ? `Previous summary:\n${previousSummary}\n\n` : ""}Transcript:\n${content}\n\n请仅总结上面的对话历史，并遵循 system 指令。` },
      ],
      tools: [],
    }, "agent");
    const result = await this.waitFor("CallLLMJob", id, 300_000);
    const summary = result.status === "completed" ? result.output?.message.content.trim() : "";
    if (!summary || result.output?.message.tool_calls?.length) return previousSummary;
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

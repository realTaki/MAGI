import { MAGI_CONTACT_ID, SYSTEM_CONTACT_ID, type Bus, type LLMMessage } from "@magi/bus";
import { SYSTEM_PROMPT } from "./prompt_defaults.js";
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const COMPACT_KEEP_RECENT = 20;
const COMPACT_CONTEXT_WINDOW = 200_000;
/** Told to the model, not enforced: a long task should stop and ask, not be cut off. */
const SUGGESTED_STEPS = 20;
/** How the model ends a turn in a room without posting anything. */
const NO_REPLY = "NO_REPLY";

export class Chat {
  private readonly labels = new Map<number, string>();

  constructor(
    private readonly bus: Bus,
    readonly chat_id: number,
  ) {}

  async run(jobId: number): Promise<void> {
    const chatBoard = this.bus.board("ChatNotify");
    try {
      const record = this.bus.chats.get(this.chat_id);
      if (!record) throw new Error("chat does not exist");
      const agentPrompt = this.bus.prompts.get("agent/AGENT") ?? "You are a helpful assistant.";
      const summary = await this.compact(record.summary);
      const memories = this.bus.memoryBook.list().map((memory) => `- [${memory.id} | ${memory.kind}] ${memory.topic}: ${memory.detail}`).join("\n");
      const skills = this.bus.skills.list().map((skill) => `- ${skill.name}: ${skill.description}`).join("\n");
      const identity = this.bus.contacts.get(MAGI_CONTACT_ID);
      // Who is in this chat: the operator, other MAGIs in a group, guests. The
      // channels record them there as they are heard from.
      const members = this.bus.chatMembers.list(this.chat_id)
        .map((member) => `- id ${member.id} | ${this.label(member.id)} | ${member.role}`).join("\n");
      const chatContext = this.context([
        ["Identity", identity ? `Your name: ${identity.nickname || identity.name}` : ""],
        ["Available skills", skills],
        ["Long-term memory", memories],
        ["Chat instruction", record.instruction],
        ["Chat info", record.info],
        ["Members", members],
        ["Prior chat summary", summary],
        ["Chat", `chat_id: ${this.chat_id}\nchannel: ${record.channel}\ndelivery_address: ${record.delivery_address}\ntopic: ${record.topic}\nhome_chat_id: ${this.bus.homeChat() ?? "none"}\nMAGI_CONTACT_ID: ${MAGI_CONTACT_ID}\nSYSTEM_CONTACT_ID: ${SYSTEM_CONTACT_ID}`],
      ]);
      const system = [agentPrompt, SYSTEM_PROMPT, chatContext].filter(Boolean).join("\n\n");
      const history = this.bus.messages.list(this.chat_id, COMPACT_KEEP_RECENT).map((message): LLMMessage => ({
        role: message.contact_id === MAGI_CONTACT_ID ? "assistant" : "user",
        content: `[contact id ${message.contact_id} | ${this.label(message.contact_id)} | ${message.created_at}]\n${message.content}`,
      }));
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
            this.bus.publishDelivery({ chat_id: this.chat_id, text: reply || "处理完毕。" });
          }
          chatBoard.submit("agent", jobId, { output: {} });
          return;
        }
        if (response.content) this.bus.publishDelivery({ chat_id: this.chat_id, text: response.content });
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
      this.bus.publishDelivery({ chat_id: this.chat_id, text: message });
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
    const content = old.map((message) => `[contact id ${message.contact_id} | ${this.label(message.contact_id)} | ${message.created_at}]\n${message.content}`).join("\n\n");
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

  /** Who said it, in the transcript the model reads: id, then name and nickname. */
  private label(contactId: number): string {
    let label = this.labels.get(contactId);
    if (label === undefined) {
      const contact = this.bus.contacts.get(contactId);
      label = [contact?.name, contact?.nickname].filter(Boolean).join(" / ") || `contact ${contactId}`;
      this.labels.set(contactId, label);
    }
    return label;
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

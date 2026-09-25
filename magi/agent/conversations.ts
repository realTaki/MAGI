import { MAGI_CONTACT_ID, SYSTEM_CONTACT_ID, type Bus, type LLMMessage } from "../bus/index.js";
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const COMPACT_KEEP_RECENT = 20;
const COMPACT_CONTEXT_WINDOW = 200_000;

export class Conversation {
  constructor(
    private readonly bus: Bus,
    readonly conversation_id: number,
  ) {}

  async run(jobId: number): Promise<void> {
    const chat = this.bus.board("ChatNotify");
    try {
      const record = this.bus.conversations.get(this.conversation_id);
      if (!record) throw new Error("conversation does not exist");
      const agentPrompt = this.bus.prompts.get("agent/AGENT") ?? "You are a helpful assistant.";
      const summary = await this.compact(record.summary);
      const memories = this.bus.memoryBook.list().map((memory) => `- [${memory.id} | ${memory.kind}] ${memory.topic}: ${memory.detail}`).join("\n");
      const skills = this.bus.skills.list().map((skill) => `- ${skill.name}: ${skill.description}`).join("\n");
      const identity = this.bus.contacts.get(MAGI_CONTACT_ID);
      const skillsHeader = this.bus.prompts.get("agent/skills_block")?.trim() || "## Available skills";
      const system = [agentPrompt, identity ? `## Identity\nYour name: ${identity.nickname || identity.name}` : "",
        skills ? `${skillsHeader}\n${skills}` : "", memories ? `## Long-term memory\n${memories}` : "",
        record.instruction ? `## Conversation instruction\n${record.instruction}` : "",
        record.info ? `## Conversation info\n${record.info}` : "",
        summary ? `[Prior conversation summary]\n${summary}` : ""]
        .filter(Boolean).join("\n\n");
      const history = this.bus.messages.list(this.conversation_id, COMPACT_KEEP_RECENT).map((message): LLMMessage => ({
        role: message.contact_id === MAGI_CONTACT_ID ? "assistant" : "user",
        content: `[contact id ${message.contact_id} | ${message.created_at}]\n${message.content}`,
      }));
      const messages: LLMMessage[] = [{ role: "system", content: `${system}\n\n## Session\nconversation_id: ${this.conversation_id}\nchannel: ${record.channel}\ndelivery_address: ${record.delivery_address}\ntopic: ${record.topic}\nMAGI_CONTACT_ID: ${MAGI_CONTACT_ID}\nSYSTEM_CONTACT_ID: ${SYSTEM_CONTACT_ID}` }, ...history];
      for (let step = 0; step < 20; step++) {
        const llmId = this.bus.board("CallLLMJob").publish({ messages, tools: this.bus.tools.catalog() }, "agent");
        const llm = await this.waitFor("CallLLMJob", llmId, 300_000);
        if (llm.status === "failed" || !llm.output?.message) throw new Error(llm.error ?? "LLM failed");
        const response = llm.output.message;
        if (!response.tool_calls?.length) {
          const deliveryId = this.bus.publishDelivery({ conversation_id: this.conversation_id, text: response.content || "处理完毕。" });
          const delivery = await this.waitFor("DeliveryNotify", deliveryId, 30_000);
          if (delivery.status === "failed") {
            chat.submit("agent", jobId, { error: delivery.error ?? "delivery failed" });
            return;
          }
          chat.submit("agent", jobId, { output: {} });
          return;
        }
        if (response.content) this.bus.publishDelivery({ conversation_id: this.conversation_id, text: response.content });
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
      throw new Error("agent exceeded 20 model steps");
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const deliveryId = this.bus.publishDelivery({ conversation_id: this.conversation_id, text: message });
      try { await this.waitFor("DeliveryNotify", deliveryId, 30_000); } catch { /* keep original failure */ }
      chat.submit("agent", jobId, { error: message });
    }
  }

  private async compact(previousSummary: string): Promise<string> {
    const active = this.bus.messages.list(this.conversation_id, 10_000);
    const estimatedTokens = active.reduce((sum, message) => sum + Math.max(1, Math.ceil(message.content.length / 4)), Math.max(0, Math.ceil(previousSummary.length / 4)));
    const configuredWindow = Number(this.bus.settings.get("provider.context_window"));
    const contextWindow = Number.isFinite(configuredWindow) && configuredWindow > 0 ? configuredWindow : COMPACT_CONTEXT_WINDOW;
    if (estimatedTokens <= Math.floor(contextWindow / 2)) return previousSummary;
    const old = active.slice(0, -COMPACT_KEEP_RECENT);
    if (!old.length) return previousSummary;
    const content = old.map((message) => `[contact ${message.contact_id} | ${message.created_at}]\n${message.content}`).join("\n\n");
    const id = this.bus.board("CallLLMJob").publish({
      messages: [
        { role: "system", content: this.bus.prompts.get("agent/compaction") ?? "Summarize the conversation." },
        { role: "user", content: `${previousSummary ? `Previous summary:\n${previousSummary}\n\n` : ""}Transcript:\n${content}\n\n请仅总结上面的对话历史，并遵循 system 指令。` },
      ],
      tools: [],
    }, "agent");
    const result = await this.waitFor("CallLLMJob", id, 300_000);
    const summary = result.status === "completed" ? result.output?.message.content.trim() : "";
    if (!summary || result.output?.message.tool_calls?.length) return previousSummary;
    this.bus.conversations.updateSummary(this.conversation_id, summary);
    this.bus.messages.archiveBefore(this.conversation_id, old.at(-1)!.id);
    return summary;
  }

  private async waitFor<K extends "CallLLMJob" | "RunToolJob" | "DeliveryNotify">(type: K, id: number, timeoutMs: number) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const result = this.bus.board(type).result(id);
      if (result) return result;
      await sleep(10);
    }
    throw new Error(`${type} timed out`);
  }
}

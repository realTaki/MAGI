import { MAGI_CONTACT_ID, SYSTEM_CONTACT_ID, type Bus, type LLMMessage, type LLMTool } from "../bus/index.js";
import { AGENT_PROMPT } from "./prompt_defaults.js";

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export class Conversation {
  constructor(
    private readonly bus: Bus,
    readonly conversation_id: number,
    private readonly tools: () => LLMTool[],
  ) {}

  async run(jobId: number): Promise<void> {
    const chat = this.bus.board("ChatNotify");
    try {
      const record = this.bus.conversations.get(this.conversation_id);
      if (!record) throw new Error("conversation does not exist");
      const system = [AGENT_PROMPT, record.instruction, record.summary ? `[Prior conversation summary]\n${record.summary}` : ""]
        .filter(Boolean).join("\n\n");
      const history = this.bus.messages.list(this.conversation_id, 20).map((message): LLMMessage => ({
        role: message.contact_id === MAGI_CONTACT_ID ? "assistant" : "user",
        content: `[contact id ${message.contact_id} | ${message.created_at}]\n${message.content}`,
      }));
      const messages: LLMMessage[] = [{ role: "system", content: `${system}\n\n## Session\nconversation_id: ${this.conversation_id}\nchannel: ${record.channel}\ndelivery_address: ${record.delivery_address}\nMAGI_CONTACT_ID: ${MAGI_CONTACT_ID}\nSYSTEM_CONTACT_ID: ${SYSTEM_CONTACT_ID}` }, ...history];
      for (let step = 0; step < 20; step++) {
        const llmId = this.bus.board("CallLLMJob").publish({ messages, tools: this.tools() }, "agent");
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
        const calls = response.tool_calls.map((call) => ({ call, jobId: this.bus.board("RunToolJob").publish({ call }, "agent") }));
        for (const pending of calls) {
          const result = await this.waitFor("RunToolJob", pending.jobId, 120_000);
          messages.push({
            role: "tool", tool_call_id: pending.call.tool_call_id,
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

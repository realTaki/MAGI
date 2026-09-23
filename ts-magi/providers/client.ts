import type { CallLLMJob, LLMMessage, LLMToolCall } from "../bus/index.js";

export interface LLMClient {
  complete(job: CallLLMJob): Promise<LLMMessage>;
  verify?(settings: ProviderSettings): Promise<void>;
  configure?(settings: ProviderSettings): void;
}

export type ProviderSettings = { provider?: string; api_key?: string; model?: string; api_base?: string };

type ResponseMessage = {
  content?: string | null;
  tool_calls?: Array<{ id?: string; type?: string; function?: { name?: string; arguments?: string } }>;
};

export class OpenAICompatibleClient implements LLMClient {
  constructor(
    private apiKey: string,
    private model: string,
    private apiBase = "https://api.openai.com/v1",
    private readonly fetcher: (url: string, init?: RequestInit) => Promise<Response> = fetch,
  ) {}

  configure(settings: ProviderSettings): void {
    if (settings.api_key !== undefined) this.apiKey = settings.api_key;
    if (settings.model !== undefined) this.model = settings.model;
    if (settings.api_base !== undefined) this.apiBase = settings.api_base;
  }

  async verify(settings: ProviderSettings): Promise<void> {
    const candidate = new OpenAICompatibleClient(
      settings.api_key ?? this.apiKey,
      settings.model ?? this.model,
      settings.api_base ?? this.apiBase,
      this.fetcher,
    );
    await candidate.request([{ role: "user", content: "Reply OK." }], [], 32);
  }

  async complete(job: CallLLMJob): Promise<LLMMessage> {
    if (!this.apiKey) throw new Error("provider.api_key is missing");
    return this.request(job.messages, job.tools);
  }

  private async request(messagesInput: LLMMessage[], tools: CallLLMJob["tools"], maxTokens?: number): Promise<LLMMessage> {
    if (!this.apiKey) throw new Error("provider.api_key is missing");
    const messages = messagesInput.map((message) => {
      if (message.role === "tool") return {
        role: "tool", tool_call_id: message.tool_call_id,
        content: message.is_error ? `Tool failed:\n${message.content}` : message.content,
      };
      if (message.role === "assistant" && message.tool_calls?.length) return {
        role: "assistant", content: message.content,
        tool_calls: message.tool_calls.map((call) => ({
          id: call.tool_call_id, type: "function",
          function: { name: call.name, arguments: JSON.stringify(call.arguments) },
        })),
      };
      return { role: message.role, content: message.content };
    });
    const response = await this.fetcher(`${this.apiBase.replace(/\/$/, "")}/chat/completions`, {
      method: "POST",
      headers: { Authorization: `Bearer ${this.apiKey}`, "Content-Type": "application/json" },
      body: JSON.stringify({ model: this.model, messages, max_tokens: maxTokens, tools: tools.length ? tools.map((tool) => ({
        type: "function", function: { name: tool.name, description: tool.description, parameters: tool.input_schema },
      })) : undefined }),
      signal: AbortSignal.timeout(120_000),
    });
    if (!response.ok) throw new Error(`provider HTTP ${response.status}: ${(await response.text()).slice(0, 500)}`);
    const body = await response.json() as { choices?: Array<{ message?: ResponseMessage }> };
    const raw = body.choices?.[0]?.message;
    if (!raw) throw new Error("provider returned no assistant message");
    if (raw.content !== null && raw.content !== undefined && typeof raw.content !== "string") throw new Error("provider returned non-text content");
    const calls: LLMToolCall[] = [];
    for (const call of raw.tool_calls ?? []) {
      if (!call.id || call.type !== "function" || !call.function?.name) throw new Error("provider returned invalid tool call");
      const parsed: unknown = JSON.parse(call.function.arguments || "{}");
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error("provider returned non-object tool arguments");
      calls.push({ tool_call_id: call.id, name: call.function.name, arguments: parsed as Record<string, unknown> });
    }
    return { role: "assistant", content: raw.content ?? "", tool_calls: calls.length ? calls : undefined };
  }
}

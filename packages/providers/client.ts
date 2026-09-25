import { createProvider, type Api, type AssistantMessage, type Context, type Message, type Model, type MutableModels, type Tool } from "@earendil-works/pi-ai";
import { openAICompletionsApi } from "@earendil-works/pi-ai/api/openai-completions.lazy";
import { builtinModels } from "@earendil-works/pi-ai/providers/all";
import type { CallLLMJob, LLMMessage, LLMToolCall } from "@magi/bus";

export interface LLMClient {
  complete(job: CallLLMJob): Promise<LLMMessage>;
  verify?(settings: ProviderSettings): Promise<void>;
  configure?(settings: ProviderSettings): void;
}

export type ProviderSettings = { provider?: string; api_key?: string; model?: string; base_url?: string };

function providerId(value: string): string {
  if (value === "claude") return "anthropic";
  // pi-ai calls MiniMax Global `minimax`; MAGI exposes the region explicitly.
  if (value === "minimax-global") return "minimax";
  return value;
}

function resolveModel(models: MutableModels, settings: ProviderSettings): Model<Api> {
  const provider = providerId(settings.provider?.trim() || "openai");
  const id = settings.model?.trim();
  if (!id) throw new Error("provider.model is missing");
  if (provider === "custom") {
    const baseUrl = settings.base_url?.trim();
    if (!baseUrl) throw new Error("provider.base_url is missing");
    const url = new URL(baseUrl);
    if (url.username || url.password || url.search || url.hash) throw new Error("custom provider URL must not contain credentials, query, or fragment");
    if (url.protocol !== "https:" && !(url.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname))) {
      throw new Error("custom provider URL must use HTTPS (or HTTP on localhost)");
    }
    const model: Model<"openai-completions"> = {
      id, name: id, api: "openai-completions", provider: "custom", baseUrl: url.toString().replace(/\/$/, ""),
      reasoning: false, input: ["text"], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: 128_000, maxTokens: 8_192,
    };
    models.setProvider(createProvider({
      id: "custom", name: "Custom", baseUrl: model.baseUrl,
      auth: { apiKey: { name: "Custom API key", async resolve() { return { auth: {} }; } } },
      models: [model], api: openAICompletionsApi(),
    }));
    return model;
  }
  const model = models.getModel(provider, id);
  if (!model) throw new Error(`pi-ai has no model ${provider}/${id}`);
  return model;
}

function toContext(job: CallLLMJob, model: Model<Api>): Context {
  const messages: Message[] = job.messages.map((message): Message => {
    const timestamp = Date.now();
    if (message.role === "system") return { role: "system", content: message.content, timestamp };
    if (message.role === "user") return { role: "user", content: message.content, timestamp };
    if (message.role === "tool") return {
      role: "toolResult", toolCallId: message.tool_call_id ?? "", toolName: message.tool_name ?? "tool",
      content: [{ type: "text", text: message.content }], isError: message.is_error ?? false, timestamp,
    };
    if (message.provider_state) return message.provider_state as unknown as AssistantMessage;
    return {
      role: "assistant", api: model.api, provider: model.provider, model: model.id,
      content: [
        ...(message.thinking_blocks ?? []).map((part) => ({ type: "thinking" as const, thinking: part.thinking, thinkingSignature: part.signature })),
        ...(message.content ? [{ type: "text" as const, text: message.content }] : []),
        ...(message.tool_calls ?? []).map((call) => ({ type: "toolCall" as const, id: call.tool_call_id, name: call.name, arguments: call.arguments as import("@earendil-works/pi-ai").JsonObject })),
      ],
      usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
      stopReason: message.tool_calls?.length ? "toolUse" : "stop", timestamp,
    };
  });
  const tools: Tool[] = job.tools.map((tool) => ({ name: tool.name, description: tool.description, parameters: tool.input_schema as Tool["parameters"] }));
  return { messages, tools };
}

export class PiAiClient implements LLMClient {
  private readonly models: MutableModels = builtinModels();
  constructor(private settings: ProviderSettings, private readonly fetcher: typeof fetch = fetch) {}

  configure(settings: ProviderSettings): void { this.settings = { ...this.settings, ...settings }; }

  async verify(settings: ProviderSettings): Promise<void> {
    await this.request({ messages: [{ role: "user", content: "Reply OK." }], tools: [] }, { ...this.settings, ...settings }, 256);
  }

  complete(job: CallLLMJob): Promise<LLMMessage> { return this.request(job, this.settings); }

  private async request(job: CallLLMJob, settings: ProviderSettings, maxTokens?: number): Promise<LLMMessage> {
    if (!settings.api_key) throw new Error("provider.api_key is missing");
    const model = resolveModel(this.models, settings);
    const response = await this.models.completeSimple(model, toContext(job, model), {
      apiKey: settings.api_key, fetch: this.fetcher, maxTokens, timeoutMs: 120_000,
    });
    if (["error", "aborted", "length", "deferred"].includes(response.stopReason)) {
      throw new Error(response.errorMessage ?? `provider stopped: ${response.stopReason}`);
    }
    const calls: LLMToolCall[] = response.content.filter((part) => part.type === "toolCall").map((part) => ({
      tool_call_id: part.id, name: part.name, arguments: part.arguments as Record<string, unknown>,
    }));
    return {
      role: "assistant",
      content: response.content.filter((part) => part.type === "text").map((part) => part.text).join(""),
      tool_calls: calls.length ? calls : undefined,
      provider_state: response as unknown as Record<string, unknown>,
    };
  }
}

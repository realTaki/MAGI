import { providerFailure, type ProviderFailureCode } from "@magi/bus/jobs/providerFailure.js";
import { createProvider, type Api, type AssistantMessage, type Context, type Message, type Model, type MutableModels, type Tool } from "@earendil-works/pi-ai";
import { openAICompletionsApi } from "@earendil-works/pi-ai/api/openai-completions.lazy";
import { builtinModels } from "@earendil-works/pi-ai/providers/all";
import type { LLMRequest, LLMMessage, LLMToolCall } from "@magi/bus";
import { MAX_ATTACHMENT_BYTES, MAX_ATTACHMENTS, nativeImageMime, readStoredAttachment } from "@magi/bus/attachments.js";

export interface LLMClient {
  complete(job: LLMRequest, signal?: AbortSignal): Promise<LLMMessage>;
  verify?(settings: ProviderSettings, signal?: AbortSignal): Promise<void>;
  configure?(settings: ProviderSettings): void;
  /** The selected model's advertised context capacity, when this client knows it. */
  contextWindow?(): number | undefined;
}

export type ProviderSettings = { provider?: string; api_key?: string; model?: string; base_url?: string };

/** Classify only known structured transport codes. Never copy error text, URLs or headers. */
export function transportFailureCode(error: unknown): ProviderFailureCode {
  const codes: Record<string, ProviderFailureCode> = {
    ENOTFOUND: "dns", EAI_AGAIN: "dns",
    ECONNREFUSED: "connection_refused",
    ETIMEDOUT: "timeout", UND_ERR_CONNECT_TIMEOUT: "timeout",
    UND_ERR_HEADERS_TIMEOUT: "timeout", UND_ERR_BODY_TIMEOUT: "timeout",
    CERT_HAS_EXPIRED: "tls", CERT_NOT_YET_VALID: "tls", DEPTH_ZERO_SELF_SIGNED_CERT: "tls",
    SELF_SIGNED_CERT_IN_CHAIN: "tls", UNABLE_TO_VERIFY_LEAF_SIGNATURE: "tls",
    UNABLE_TO_GET_ISSUER_CERT: "tls", UNABLE_TO_GET_ISSUER_CERT_LOCALLY: "tls",
    ERR_TLS_CERT_ALTNAME_INVALID: "tls", ERR_SSL_WRONG_VERSION_NUMBER: "tls",
    ERR_SSL_TLSV1_ALERT_PROTOCOL_VERSION: "tls", ERR_SSL_SSLV3_ALERT_HANDSHAKE_FAILURE: "tls",
  };
  const pending: unknown[] = [error];
  const seen = new Set<object>();
  const found = new Set<ProviderFailureCode>();
  // Node fetch wraps socket errors in cause, including AggregateError for dual-stack attempts.
  // Bound traversal and return generic connection for conflicting or incomplete evidence.
  let inspected = 0;
  while (pending.length && inspected++ < 16) {
    const value = pending.shift();
    if (!value || typeof value !== "object" || seen.has(value)) continue;
    seen.add(value);
    const detail = value as { code?: unknown; name?: unknown; cause?: unknown; errors?: unknown };
    if (typeof detail.code === "string" && Object.hasOwn(codes, detail.code)) found.add(codes[detail.code]!);
    if (detail.name === "AbortError" || detail.name === "TimeoutError") found.add("timeout");
    if (detail.cause) pending.push(detail.cause);
    if (Array.isArray(detail.errors)) pending.push(...detail.errors.slice(0, 16));
  }
  return !pending.length && found.size === 1 ? [...found][0]! : "connection";
}

function providerId(value: string): string {
  if (value === "claude") return "anthropic";
  // pi-ai calls MiniMax Global `minimax`; MAGI exposes the region explicitly.
  if (value === "minimax-global") return "minimax";
  return value;
}

function resolveModel(models: MutableModels, settings: ProviderSettings): Model<Api> {
  const provider = providerId(settings.provider?.trim() || "openai");
  const id = settings.model?.trim();
  if (!id) throw providerFailure("model");
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
      // A custom endpoint has no catalog metadata.  Treat it as reasoning-capable so
      // pi-ai can use the standard OpenAI-compatible capability when the endpoint
      // supports it; its compatibility adapter omits unsupported request fields.
      reasoning: true, input: ["text"], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
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
  if (!model) throw providerFailure("model");
  return model;
}

function toContext(job: LLMRequest, model: Model<Api>, workspace?: string): Context {
  let imageBytes = 0;
  let imageCount = 0;
  const selectedImages = new Map<LLMMessage, import("@earendil-works/pi-ai").ImageContent[]>();
  if (workspace && model.input.includes("image")) {
    const perMessage = Math.min(MAX_ATTACHMENTS, model.inputLimits?.images?.maxPerMessage ?? MAX_ATTACHMENTS);
    const perRequest = Math.min(MAX_ATTACHMENTS, model.inputLimits?.images?.maxPerRequest ?? MAX_ATTACHMENTS);
    // Favor the newest attachments when conversation history exceeds a provider's
    // limits; older messages keep their stable workspace-path text.
    for (const message of [...job.messages].reverse()) {
      if (message.role !== "user" || typeof message.attachment_scope !== "string" || !Array.isArray(message.attachments)) continue;
      const images: import("@earendil-works/pi-ai").ImageContent[] = [];
      for (const attachment of message.attachments.slice(0, MAX_ATTACHMENTS)) {
        if (images.length >= perMessage || imageCount >= perRequest) break;
        try {
          if (!attachment.mime_type.startsWith("image/")) continue;
          // A conservative per-image bound also fits providers with a 5 MiB
          // image limit. Respect tighter catalog base64 limits where supplied.
          const encodedLimit = model.inputLimits?.images?.resize?.maxBytes;
          const readLimit = Math.min(5 * 1024 * 1024, MAX_ATTACHMENT_BYTES - imageBytes,
            encodedLimit === undefined ? Infinity : Math.floor(encodedLimit * 3 / 4));
          const data = readStoredAttachment(workspace, message.attachment_scope, attachment, readLimit);
          const mimeType = nativeImageMime(data, attachment.mime_type);
          if (!mimeType) continue;
          images.push({ type: "image", data: data.toString("base64"), mimeType });
          imageBytes += data.length;
          imageCount++;
        } catch { /* Deleted/unsafe/oversize files retain the ordinary text-path fallback. */ }
      }
      if (images.length) selectedImages.set(message, images);
    }
  }
  const messages: Message[] = job.messages.map((message): Message => {
    const timestamp = Date.now();
    if (message.role === "system") return { role: "system", content: message.content, timestamp };
    if (message.role === "user") {
      const images = selectedImages.get(message) ?? [];
      return { role: "user", content: images.length ? [{ type: "text", text: message.content }, ...images] : message.content, timestamp };
    }
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
  constructor(private settings: ProviderSettings, private readonly fetcher: typeof fetch = fetch, private readonly workspace?: string) {}

  configure(settings: ProviderSettings): void { this.settings = { ...this.settings, ...settings }; }

  async verify(settings: ProviderSettings, signal?: AbortSignal): Promise<void> {
    await this.request({ messages: [{ role: "user", content: "Reply OK." }], tools: [] }, { ...this.settings, ...settings }, 256, signal);
  }

  complete(job: LLMRequest, signal?: AbortSignal): Promise<LLMMessage> { return this.request(job, this.settings, undefined, signal); }

  contextWindow(): number | undefined {
    // A provider with no configured model is a normal fresh-workspace state, not an
    // error for the worker starting up.
    if (!this.settings.model?.trim()) return undefined;
    return resolveModel(this.models, this.settings).contextWindow;
  }

  private async request(job: LLMRequest, settings: ProviderSettings, maxTokens?: number, signal?: AbortSignal): Promise<LLMMessage> {
    if (!settings.api_key?.trim()) throw providerFailure("unconfigured");
    let model: Model<Api>;
    try { model = resolveModel(this.models, settings); }
    catch (error) { if (error instanceof Error && error.message.startsWith("ProviderError")) throw error; throw providerFailure("configuration"); }
    let failure: ProviderFailureCode | undefined;
    const fetcher: typeof fetch = async (input, init) => {
      try {
        const response = await this.fetcher(input, init);
        if (!response.ok) {
          failure = response.status === 401 || response.status === 403 ? "authentication"
            : response.status === 429 || response.status === 402 ? "rate_limit"
            : response.status === 408 || response.status === 504 ? "timeout"
            : response.status >= 500 && response.status <= 599 ? "server" : "configuration";
          // Inspect only error semantics; never surface or persist a body, URL, header or key.
          try {
            const body = await response.clone().json() as { error?: { type?: string; code?: string; message?: string } };
            const detail = `${body.error?.type ?? ""} ${body.error?.code ?? ""} ${body.error?.message ?? ""}`;
            if ([400, 404, 422].includes(response.status) && /model[_ ]not[_ ]found|unsupported[_ ]model|model.*(not exist|not found|not supported|not available)/i.test(detail)) failure = "model";
          } catch { /* Non-JSON error responses retain the HTTP classification. */ }
        }
        return response;
      } catch (error) {
        failure = signal?.aborted ? "timeout" : transportFailureCode(error);
        throw providerFailure(failure);
      }
    };
    let response: AssistantMessage;
    try { response = await this.models.completeSimple(model, toContext(job, model, this.workspace), {
      // pi-ai maps this neutral level to each provider's native reasoning API and
      // ignores it for models which do not advertise reasoning support.  Thinking
      // remains internal: only ordinary text is delivered to the user.
      reasoning: maxTokens ? undefined : "medium",
      apiKey: settings.api_key.trim(), fetch: fetcher, maxTokens, signal, maxRetries: 0, timeoutMs: maxTokens ? 20_000 : 120_000,
    }); } catch { throw providerFailure(failure ?? "unknown"); }
    if (failure) throw providerFailure(failure);
    if (["error", "aborted", "deferred"].includes(response.stopReason) || (!maxTokens && response.stopReason === "length")) {
      const detail = response.errorMessage ?? "";
      const code = signal?.aborted || response.stopReason === "aborted" ? "timeout"
        : /"type"\s*:\s*"(?:authentication_error|permission_error)"/.test(detail) ? "authentication"
        : /model[_ ]not[_ ]found|unsupported[_ ]model/.test(detail) ? "model"
        : /stream ended before|connection error|fetch failed|terminated/i.test(detail) ? "connection" : "unknown";
      throw providerFailure(code);
    }
    // A completed short probe reaching its output limit still proves authentication/model access.
    // A normal completion must never silently accept truncation.

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

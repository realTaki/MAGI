import { BaseWorker, type CallLLMJob, type ChangeProviderNotify } from "../bus/index.js";
import type { Bus } from "../bus/index.js";
import { OpenAICompatibleClient, type LLMClient, type ProviderSettings } from "./client.js";

export class ProvidersWorker extends BaseWorker {
  readonly worker_name = "providers";
  private readonly client: LLMClient;

  constructor(bus: Bus, client?: LLMClient) {
    super(bus);
    this.client = client ?? new OpenAICompatibleClient(
      bus.getSetting("provider.api_key") ?? process.env.MAGI_API_KEY ?? "",
      bus.getSetting("provider.model") ?? process.env.MAGI_MODEL ?? "gpt-4.1-mini",
      bus.getSetting("provider.api_base") ?? process.env.MAGI_API_BASE ?? "https://api.openai.com/v1",
    );
  }

  async poll(): Promise<boolean> {
    const changes = this.bus.board("ChangeProviderNotify");
    const change = changes.claim(this.worker_name);
    if (change) {
      await this.change(change.id, change.input);
      return true;
    }
    const board = this.bus.board("CallLLMJob");
    const job = board.claim(this.worker_name);
    if (!job) return false;
    try {
      const message = await this.client.complete(job.input as CallLLMJob);
      board.submit(this.worker_name, job.id, { output: { message } });
    } catch (error) {
      board.submit(this.worker_name, job.id, { error: error instanceof Error ? error.message : String(error) });
    }
    return true;
  }

  private async change(id: number, settings: ChangeProviderNotify): Promise<void> {
    const board = this.bus.board("ChangeProviderNotify");
    const candidate: ProviderSettings = {
      provider: settings.provider,
      api_key: settings.api_key,
      model: settings.model,
      api_base: providerBase(settings.provider) ?? undefined,
    };
    try {
      if (!this.client.verify || !this.client.configure) throw new Error("provider client cannot be reconfigured");
      await this.client.verify(candidate);
      this.client.configure(candidate);
      if (settings.provider !== undefined) this.bus.setSetting("provider.name", settings.provider);
      if (settings.api_key !== undefined) this.bus.setSetting("provider.api_key", settings.api_key);
      if (settings.model !== undefined) this.bus.setSetting("provider.model", settings.model);
      if (candidate.api_base !== undefined) this.bus.setSetting("provider.api_base", candidate.api_base);
      board.submit(this.worker_name, id, { output: {} });
    } catch (error) {
      const raw = error instanceof Error ? error.message : String(error);
      const redacted = settings.api_key ? raw.replaceAll(settings.api_key, "[redacted]") : raw;
      board.submit(this.worker_name, id, { error: redacted });
    }
  }
}

function providerBase(provider?: string): string | null {
  switch (provider?.trim().toLowerCase()) {
    case undefined:
    case "": return null;
    case "openai": return "https://api.openai.com/v1";
    case "xai": return "https://api.x.ai/v1";
    case "deepseek": return "https://api.deepseek.com/v1";
    case "gemini": return "https://generativelanguage.googleapis.com/v1beta/openai";
    case "minimax":
    case "minimax-cn": return "https://api.minimaxi.com/v1";
    case "minimax-global": return "https://api.minimax.io/v1";
    default: throw new Error(`provider ${provider} is not supported by the TypeScript runtime`);
  }
}

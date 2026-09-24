import { BaseWorker, type CallLLMJob, type ChangeProviderNotify } from "../bus/index.js";
import type { Bus } from "../bus/index.js";
import { PiAiClient, type LLMClient, type ProviderSettings } from "./client.js";

export class ProvidersWorker extends BaseWorker {
  readonly worker_name = "providers";
  private readonly client: LLMClient;

  constructor(bus: Bus, client?: LLMClient) {
    super(bus);
    this.client = client ?? new PiAiClient({
      api_key: bus.getSetting("provider.api_key") ?? process.env.MAGI_API_KEY ?? "",
      model: bus.getSetting("provider.model") ?? process.env.MAGI_MODEL ?? "gpt-4.1-mini",
      base_url: bus.getSetting("provider.base_url") ?? bus.getSetting("provider.api_base") ?? process.env.MAGI_API_BASE ?? "",
      provider: bus.getSetting("provider.name") ?? process.env.MAGI_PROVIDER ?? (process.env.MAGI_API_BASE ? "custom" : "openai"),
    });
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
      base_url: settings.base_url,
    };
    try {
      if (!this.client.verify || !this.client.configure) throw new Error("provider client cannot be reconfigured");
      await this.client.verify(candidate);
      this.client.configure(candidate);
      if (settings.provider !== undefined) this.bus.setSetting("provider.name", settings.provider);
      if (settings.api_key !== undefined) this.bus.setSetting("provider.api_key", settings.api_key);
      if (settings.model !== undefined) this.bus.setSetting("provider.model", settings.model);
      if (candidate.base_url !== undefined) this.bus.setSetting("provider.base_url", candidate.base_url);
      board.submit(this.worker_name, id, { output: {} });
    } catch (error) {
      const raw = error instanceof Error ? error.message : String(error);
      const redacted = settings.api_key ? raw.replaceAll(settings.api_key, "[redacted]") : raw;
      board.submit(this.worker_name, id, { error: redacted });
    }
  }
}

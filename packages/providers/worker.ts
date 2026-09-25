import { BaseWorker, type CallLLMJob, type ChangeProviderNotify } from "@magi/bus";
import type { Bus } from "@magi/bus";
import { PiAiClient, type LLMClient, type ProviderSettings } from "./client.js";

export class ProvidersWorker extends BaseWorker {
  readonly worker_name = "providers";
  private readonly client: LLMClient;

  constructor(bus: Bus, client?: LLMClient) {
    super(bus);
    // A freshly started MAGI has no provider at all: these settings belong to
    // this workspace and the operator's app writes them through ASP
    // (ChangeProviderNotify). Nothing else configures them — no environment,
    // no default provider, no default model.
    this.client = client ?? new PiAiClient({
      provider: bus.settings.get("provider.name") ?? undefined,
      api_key: bus.settings.get("provider.api_key") ?? undefined,
      model: bus.settings.get("provider.model") ?? undefined,
      base_url: bus.settings.get("provider.base_url") ?? bus.settings.get("provider.api_base") ?? undefined,
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
      if (settings.provider !== undefined) this.bus.settings.set("provider.name", settings.provider);
      if (settings.api_key !== undefined) this.bus.settings.set("provider.api_key", settings.api_key);
      if (settings.model !== undefined) this.bus.settings.set("provider.model", settings.model);
      if (candidate.base_url !== undefined) this.bus.settings.set("provider.base_url", candidate.base_url);
      board.submit(this.worker_name, id, { output: {} });
    } catch (error) {
      const raw = error instanceof Error ? error.message : String(error);
      const redacted = settings.api_key ? raw.replaceAll(settings.api_key, "[redacted]") : raw;
      board.submit(this.worker_name, id, { error: redacted });
    }
  }
}

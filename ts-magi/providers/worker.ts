import { BaseWorker, type CallLLMJob } from "../bus/index.js";
import type { Bus } from "../bus/index.js";
import { OpenAICompatibleClient, type LLMClient } from "./client.js";

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
}

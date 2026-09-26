import { BaseWorker, type CallLLMJob, type ExecutableTool } from "@magi/bus";
import type { Bus } from "@magi/bus";
import { PiAiClient, type LLMClient, type ProviderSettings } from "./client.js";
import { providerTools } from "./tools.js";

export class ProvidersWorker extends BaseWorker {
  readonly worker_name = "providers";
  private readonly client: LLMClient;
  /** What is active now: both `ChangeProviderNotify` and `provider_settings` move it. */
  private settings: ProviderSettings;
  private readonly own: ExecutableTool[];
  private started = false;

  constructor(bus: Bus, client?: LLMClient) {
    super(bus);
    // A freshly started MAGI has no provider at all: these settings belong to
    // this workspace and the operator's app writes them through ASP
    // (ChangeProviderNotify), while the model changes them with the
    // `provider_settings` tool below. Nothing else configures them — no
    // environment, no default provider, no default model.
    this.settings = {
      provider: bus.settings.get("provider.name") ?? undefined,
      api_key: bus.settings.get("provider.api_key") ?? undefined,
      model: bus.settings.get("provider.model") ?? undefined,
      base_url: bus.settings.get("provider.base_url") ?? bus.settings.get("provider.api_base") ?? undefined,
    };
    this.client = client ?? new PiAiClient(this.settings);
    this.own = providerTools(() => this.settings, (settings) => this.apply(settings));
    // Offered only while this worker runs: a stopped worker would leave the model
    // holding a tool whose changes nobody verifies.
    bus.tools.registerSource("providers", () => (this.started ? this.own : []));
  }

  async start(): Promise<void> {
    this.started = true;
    this.syncContextWindow();
  }

  async poll(): Promise<boolean> {
    if (await this.pollChange()) return true;
    if (await this.pollToolCall()) return true;
    return this.pollCompletion();
  }

  /** The ASP channel asking for a change: a job, because that crosses workers. */
  private async pollChange(): Promise<boolean> {
    const changes = this.bus.board("ChangeProviderNotify");
    const change = changes.claim(this.worker_name);
    if (!change) return false;
    try {
      await this.apply(change.input);
      changes.submit(this.worker_name, change.id, { output: {} });
    } catch (error) {
      changes.submit(this.worker_name, change.id, { error: message(error) });
    }
    return true;
  }

  /** `provider_settings` runs here, like any tool a worker owns the name of. */
  private async pollToolCall(): Promise<boolean> {
    const board = this.bus.board("RunToolJob");
    const job = board.claim(this.worker_name, (input) => this.own.some((tool) => tool.name === input.call.name));
    if (!job) return false;
    const tool = this.own.find((candidate) => candidate.name === job.input.call.name);
    try {
      if (!tool) throw new Error(`unknown tool ${job.input.call.name}`);
      board.submit(this.worker_name, job.id, { output: { content: await tool.run(job.input.call.arguments) } });
    } catch (error) {
      board.submit(this.worker_name, job.id, { error: message(error) });
    }
    return true;
  }

  private async pollCompletion(): Promise<boolean> {
    const board = this.bus.board("CallLLMJob");
    const job = board.claim(this.worker_name);
    if (!job) return false;
    try {
      const turn_id = (job.input as CallLLMJob).turn_id;
      // An append may have survived a process exit immediately before this Job was
      // marked complete. Reuse it rather than asking the provider a second time.
      let message: import("@magi/bus").LLMMessage;
      try { message = this.bus.agentTurns.assistant(turn_id, job.id); }
      catch {
        message = await this.client.complete(this.bus.agentTurns.get(turn_id));
        this.bus.agentTurns.appendAssistant(turn_id, job.id, message);
      }
      board.submit(this.worker_name, job.id, { output: { turn_id } });
    } catch (error) {
      board.submit(this.worker_name, job.id, { error: message(error) });
    }
    return true;
  }

  /**
   * Verify a candidate before it becomes the active provider, so a bad key never
   * replaces a working one. A field the caller left out keeps its current value,
   * and a failure is reported with the key taken out of it.
   */
  private async apply(change: ProviderSettings): Promise<void> {
    const candidate: ProviderSettings = {
      provider: change.provider ?? this.settings.provider,
      api_key: change.api_key ?? this.settings.api_key,
      model: change.model ?? this.settings.model,
      base_url: change.base_url ?? this.settings.base_url,
    };
    try {
      if (!this.client.verify || !this.client.configure) throw new Error("provider client cannot be reconfigured");
      await this.client.verify(candidate);
      this.client.configure(candidate);
    } catch (error) {
      throw new Error(redact(message(error), candidate.api_key));
    }
    this.settings = candidate;
    if (change.provider !== undefined) this.bus.settings.set("provider.name", change.provider);
    if (change.api_key !== undefined) this.bus.settings.set("provider.api_key", change.api_key);
    if (change.model !== undefined) this.bus.settings.set("provider.model", change.model);
    if (change.base_url !== undefined) this.bus.settings.set("provider.base_url", change.base_url);
    this.syncContextWindow();
  }

  /**
   * The agent owns compaction, while pi-ai owns model metadata.  Bridge the two
   * once a provider is active so users never have to enter a context limit by hand.
   * An injected/non-pi client simply leaves an existing explicit setting alone.
   */
  private syncContextWindow(): void {
    try {
      const contextWindow = this.client.contextWindow?.();
      if (typeof contextWindow === "number" && Number.isSafeInteger(contextWindow) && contextWindow > 0) {
        this.bus.settings.set("provider.context_window", String(contextWindow));
      }
    } catch {
      // Keep the worker available to report/configure a previously saved bad model.
    }
  }

  async stop(): Promise<void> { this.started = false; }
}

/** A rejected key belongs in no message: the model or the operator reads this one. */
function redact(text: string, secret?: string): string {
  return secret ? text.replaceAll(secret, "[redacted]") : text;
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

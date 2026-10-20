import { abortable } from "@magi/bus/abort.js";
import { providerFailure } from "@magi/bus/jobs/providerFailure.js";
import { BaseWorker, waitForJob, type CallLLMJob, type ExecutableTool } from "@magi/bus";
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
  private paused = false;
  private current: AbortController | null = null;
  private readonly active = new Map<number, { background: boolean; controller: AbortController; work: Promise<void> }>();

  constructor(bus: Bus, client?: LLMClient, private readonly verifyTimeoutMs = 20_000) {
    super(bus);
    // A freshly started MAGI has no provider until it configures itself.
    // The key lives in the secret store. The model changes the rest with
    // `provider_settings`, which the manager writes. Nothing reads the
    // environment and there is no default provider.
    this.settings = this.readSettings();
    this.client = client ?? new PiAiClient(this.settings);
    this.own = providerTools(() => this.settings, (settings) => this.apply(settings));
    // Offered only while this worker runs: a stopped worker would leave the model
    // holding a tool whose changes nobody verifies.
    bus.tools.registerSource("providers", () => (this.started ? this.own : []));
  }

  async start(): Promise<void> {
    this.started = true;
    this.paused = false;
    this.settings = this.readSettings();
    if (this.settings.provider && this.settings.api_key && this.client.configure) {
      this.client.configure(this.settings);
    }
    this.syncContextWindow();
  }

  async poll(): Promise<boolean> {
    if (this.paused) return false;
    if (this.active.size && (this.bus.board("ChangeProviderNotify").hasPending()
      || this.bus.board("RunToolJob").hasPending(input => this.own.some(tool => tool.name === input.call.name)))) return false;
    // Configuration cannot mutate the shared client while either lane uses it.
    if (!this.active.size && await this.pollChange()) return true;
    if (!this.active.size && await this.pollToolCall()) return true;
    return this.pollCompletion();
  }

  /** Another worker asking for a change: a job, because that crosses workers. */
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
    const foregroundBusy = [...this.active.values()].some(call => !call.background);
    const backgroundBusy = [...this.active.values()].some(call => call.background);
    // One foreground slot is never consumed by background work. Prefer a waiting
    // foreground call, but permit one low-traffic internal call alongside it.
    const job = (!foregroundBusy ? board.claim(this.worker_name, input => !input.background) : null)
      ?? (!backgroundBusy ? board.claim(this.worker_name, input => input.background === true) : null);
    if (!job) return false;
    const controller = new AbortController();
    const work = this.complete(job.id, job.input, controller);
    this.active.set(job.id, { background: job.input.background === true, controller, work });
    void work.finally(() => this.active.delete(job.id));
    return true;
  }

  private async complete(jobId: number, input: CallLLMJob, controller: AbortController): Promise<void> {
    const board = this.bus.board("CallLLMJob");
    try {
      const { turn_id } = input;
      let message: import("@magi/bus").LLMMessage;
      try { message = this.bus.agentTurns.assistant(turn_id, jobId); }
      catch {
        message = await abortable(this.client.complete(this.bus.agentTurns.get(turn_id), controller.signal), controller.signal);
        controller.signal.throwIfAborted();
        this.bus.agentTurns.appendAssistant(turn_id, jobId, message);
      }
      board.submit(this.worker_name, jobId, { output: { turn_id } });
    } catch (error) {
      board.submit(this.worker_name, jobId, { error: message(error) });
    }
  }

  private readSettings(): ProviderSettings {
    return {
      provider: this.bus.settings.get("provider.name") ?? undefined,
      api_key: this.bus.secrets.get("provider.api_key") ?? undefined,
      model: this.bus.settings.get("provider.model") ?? undefined,
      base_url: this.bus.settings.get("provider.base_url") ?? undefined,
    };
  }

  /**
   * Verify a candidate before it becomes the active provider, so a bad key never
   * replaces a working one. A field the caller left out keeps its current value.
   * The manager writes the result; this worker only verifies and then uses it.
   */
  private async apply(change: ProviderSettings): Promise<void> {
    const candidate: ProviderSettings = {
      provider: change.provider ?? this.settings.provider,
      api_key: change.api_key ?? this.bus.secrets.get("provider.api_key") ?? this.settings.api_key,
      model: change.model ?? this.settings.model,
      base_url: change.base_url ?? this.settings.base_url,
    };
    try {
      if (!this.client.verify || !this.client.configure) throw new Error("provider client cannot be reconfigured");
      const controller = new AbortController();
      this.current = controller;
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        await Promise.race([
          abortable(this.client.verify(candidate, controller.signal), controller.signal),
          new Promise<never>((_resolve, reject) => { timer = setTimeout(() => { controller.abort(providerFailure("timeout")); reject(providerFailure("timeout")); }, this.verifyTimeoutMs); }),
        ]);
      } finally { if (timer) clearTimeout(timer); if (this.current === controller) this.current = null; }
      controller.signal.throwIfAborted();
    } catch (error) {
      throw new Error(redact(message(error), candidate.api_key));
    }
    if (this.paused) throw new Error("MAGI stopped");
    const settings: Record<string, string> = {};
    if (change.provider !== undefined) settings["provider.name"] = change.provider;
    if (change.model !== undefined) settings["provider.model"] = change.model;
    if (change.base_url !== undefined) settings["provider.base_url"] = change.base_url;
    const secrets = change.api_key !== undefined ? { "provider.api_key": change.api_key } : undefined;
    const id = this.bus.board("ConfigureJob").publish({ settings, secrets }, this.worker_name);
    let stored;
    try { stored = await waitForJob(() => this.bus.board("ConfigureJob").result(id), 30_000, "ConfigureJob"); }
    catch {
      // A timed-out write must not become a later surprise save.
      stored = this.bus.board("ConfigureJob").result(id);
      if (!stored) { this.bus.board("ConfigureJob").fail(id, "Provider configuration write timed out"); throw providerFailure("timeout"); }
    }
    if (stored.status === "failed") throw new Error(stored.error ?? "configure failed");
    this.client.configure!(candidate);
    this.settings = candidate;
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

  interrupt(): void {
    this.paused = true;
    this.current?.abort(new Error("MAGI stopped"));
    for (const call of this.active.values()) call.controller.abort(new Error("MAGI stopped"));
  }

  async stop(): Promise<void> { await Promise.all([...this.active.values()].map(call => call.work)); this.started = false; }
}

/** A rejected key belongs in no message: the model or the operator reads this one. */
function redact(text: string, secret?: string): string {
  return secret ? text.replaceAll(secret, "[redacted]") : text;
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

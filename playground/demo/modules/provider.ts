import {
  BaseWorker,
  CallLLMJob,
  GetSettingJob,
  slot,
  type Slot,
} from "../../src/index.js";

export class DemoProvider extends BaseWorker {
  readonly workerName = "provider";
  readonly requiredSlots: readonly Slot[] = [
    slot(CallLLMJob, "claim"),
    slot(CallLLMJob, "submitResult"),
    slot(GetSettingJob, "publish"),
    slot(GetSettingJob, "getResult"),
  ];

  model: string | null = null;

  protected override async onAttached(): Promise<void> {
    await this.bus.boostDefaultSettings({ model: "demo-model" });
    const settings = this.bus.board(GetSettingJob);
    const jobId = await settings.publish({ key: "provider.model" });
    this.model = (await settings.getResult(jobId))?.output?.value ?? null;
  }

  /** Placeholder inference: real provider SDK code belongs only here. */
  async serveNext(): Promise<number | null> {
    const jobs = this.bus.board(CallLLMJob);
    const job = await jobs.claim();
    if (!job) return null;
    await jobs.submitResult(job.id, {
      output: {
        text: `demo response to: ${job.input.messages.at(-1)?.content ?? ""}`,
        model: this.model ?? "unknown",
      },
    });
    return job.id;
  }
}

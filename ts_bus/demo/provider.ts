import {
  BaseWorker,
  CallLLMJob,
  GetSettingJob,
  slot,
  type Slot,
} from "../src/index.js";

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
}

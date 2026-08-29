import {
  BaseWorker,
  CallLLMJob,
  slot,
  type JobResult,
  type Slot,
} from "@magi/bus";

/** A delivery module: it can observe completed calls but cannot publish or execute them. */
export class ResultReader extends BaseWorker {
  readonly workerName = "result-reader";
  readonly requiredSlots: readonly Slot[] = [slot(CallLLMJob, "getResult")];

  read(jobId: number): Promise<JobResult<typeof CallLLMJob> | null> {
    return this.bus.board(CallLLMJob).getResult(jobId);
  }
}

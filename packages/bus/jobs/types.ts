/**
 * The board contract: which job types exist, what each carries in and out, and
 * what a claimed job looks like.
 *
 * Each payload lives in its own file beside this one (`chatNotify.ts`,
 * `callLlm.ts`, …), so a reader opens the job they care about instead of
 * scanning one long list. This file only wires them into the board.
 */

import type { CallLLMJob, CallLLMResult } from "./callLlm.js";
import type { ChangeMcpServerNotify } from "./changeMcpServer.js";
import type { ChangeProviderNotify } from "./changeProvider.js";
import type { ChatNotify } from "./chatNotify.js";
import type { DeliveryNotify } from "./deliveryNotify.js";
import type { ManageWorkerNotify } from "./manageWorker.js";
import type { RunToolJob, RunToolResult } from "./runTool.js";

/** Job type -> what its publisher hands to the board. */
export type JobInput = {
  ChatNotify: ChatNotify;
  CallLLMJob: CallLLMJob;
  RunToolJob: RunToolJob;
  DeliveryNotify: DeliveryNotify;
  ChangeProviderNotify: ChangeProviderNotify;
  ChangeMcpServerNotify: ChangeMcpServerNotify;
  ManageWorkerNotify: ManageWorkerNotify;
};

/** Job type -> what the worker writes back. Empty where the job only acts. */
export type JobOutput = {
  ChatNotify: Record<string, never>;
  CallLLMJob: CallLLMResult;
  RunToolJob: RunToolResult;
  DeliveryNotify: Record<string, never>;
  ChangeProviderNotify: Record<string, never>;
  ChangeMcpServerNotify: Record<string, never>;
  ManageWorkerNotify: { running: string[] };
};

export type JobType = keyof JobInput;
export type JobStatus = "pending" | "claimed" | "completed" | "failed";
export type Job<K extends JobType> = { id: number; type: K; input: JobInput[K]; status: JobStatus; worker?: string };
export type JobResult<K extends JobType> = { id: number; status: "completed" | "failed"; output?: JobOutput[K]; error?: string };

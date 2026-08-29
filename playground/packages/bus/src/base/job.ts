export type BoardOperation = "publish" | "claim" | "submitResult" | "getResult";

export type JobType<Input, Output> = {
  readonly name: string;
  readonly __input?: Input;
  readonly __output?: Output;
};

export type AnyJobType = JobType<unknown, unknown>;
export type InputOf<T extends AnyJobType> = T extends JobType<infer Input, unknown>
  ? Input
  : never;
export type OutputOf<T extends AnyJobType> = T extends JobType<unknown, infer Output>
  ? Output
  : never;

export type Job<T extends AnyJobType> = { id: number; input: InputOf<T> };
export type JobResult<T extends AnyJobType> = {
  id: number;
  status: "completed" | "failed";
  output?: OutputOf<T>;
  error?: string;
};

export type Slot = { job: string; operation: BoardOperation };

export type JobBoard<T extends AnyJobType> = {
  readonly type: T;
  readonly operations: ReadonlySet<BoardOperation>;
  publish(input: InputOf<T>, publisher: string): number;
  claim(worker: string): Job<T> | null;
  submitResult(
    worker: string,
    jobId: number,
    result: { output?: OutputOf<T>; error?: string },
  ): boolean;
  getResult(jobId: number): JobResult<T> | null;
};

export const defineJob = <Input, Output>(name: string): JobType<Input, Output> => ({ name });
export const slot = (job: AnyJobType, operation: BoardOperation): Slot => ({
  job: job.name,
  operation,
});

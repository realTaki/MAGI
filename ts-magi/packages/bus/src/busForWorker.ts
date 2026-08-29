import {
  type AnyJobType,
  type InputOf,
  type Job,
  type JobBoard,
  type JobResult,
  type OutputOf,
  type Slot,
} from "./base/job.js";

export class JobBoardClient<T extends AnyJobType> {
  constructor(
    private readonly board: JobBoard<T>,
    private readonly workerId: string,
    private readonly allowed: ReadonlySet<Slot["operation"]>,
  ) {}

  publish(input: InputOf<T>): Promise<number> {
    this.#require("publish");
    return Promise.resolve(this.board.publish(input, this.workerId));
  }

  claim(): Promise<Job<T> | null> {
    this.#require("claim");
    return Promise.resolve(this.board.claim(this.workerId));
  }

  submitResult(
    jobId: number,
    result: { output?: OutputOf<T>; error?: string },
  ): Promise<boolean> {
    this.#require("submitResult");
    return Promise.resolve(this.board.submitResult(this.workerId, jobId, result));
  }

  getResult(jobId: number): Promise<JobResult<T> | null> {
    this.#require("getResult");
    return Promise.resolve(this.board.getResult(jobId));
  }

  #require(operation: Slot["operation"]): void {
    if (!this.allowed.has(operation)) {
      throw new Error(`${this.workerId} has no ${this.board.type.name}.${operation} slot`);
    }
  }
}

export class BusForWorker {
  #attached = true;

  constructor(
    readonly workerId: string,
    private readonly slots: readonly Slot[],
    private readonly findBoard: (job: string) => JobBoard<AnyJobType>,
    private readonly boost: (namespace: string, values: Record<string, string>) => Promise<void>,
    private readonly release: () => void,
  ) {}

  board<T extends AnyJobType>(type: T): JobBoardClient<T> {
    if (!this.#attached) throw new Error(`${this.workerId} is detached`);
    const allowed = new Set(
      this.slots.filter((item) => item.job === type.name).map((item) => item.operation),
    );
    if (allowed.size === 0) throw new Error(`${this.workerId} has no slots for ${type.name}`);
    return new JobBoardClient(this.findBoard(type.name) as JobBoard<T>, this.workerId, allowed);
  }

  boostDefaultSettings(values: Record<string, string>): Promise<void> {
    if (!this.#attached) throw new Error(`${this.workerId} is detached`);
    return this.boost(this.workerId, values);
  }

  detach(): void {
    if (!this.#attached) return;
    this.#attached = false;
    this.release();
  }
}

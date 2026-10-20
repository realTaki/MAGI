/** Stop awaiting an operation on cancellation, consuming any late rejection safely. */
export function abortable<T>(operation: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) { void operation.catch(() => {}); return Promise.reject(signal.reason); }
  return new Promise<T>((resolve, reject) => {
    const aborted = () => { signal.removeEventListener("abort", aborted); reject(signal.reason); };
    signal.addEventListener("abort", aborted, { once: true });
    operation.then((value) => { signal.removeEventListener("abort", aborted); resolve(value); },
      (error) => { signal.removeEventListener("abort", aborted); reject(error); });
  });
}

/** A grace period, not cancellation: unfinished mutations retain their owner. */
export const MUTATION_SETTLE_MS = 1_000;

/** Bound an ownership wait without cancelling or discarding the underlying task. */
export function settleWithin<T>(operation: Promise<T>, timeoutMs: number, error: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(error)), timeoutMs);
    operation.then((value) => { clearTimeout(timer); resolve(value); },
      (cause) => { clearTimeout(timer); reject(cause); });
  });
}

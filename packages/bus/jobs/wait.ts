const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Poll a job result until it exists. The caller reports the thrown timeout. */
export async function waitForJob<T>(read: () => T | null, timeoutMs: number, label: string, signal?: AbortSignal): Promise<T> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    signal?.throwIfAborted();
    const result = read();
    if (result) return result;
    await sleep(20);
  }
  throw new Error(`${label} timed out`);
}

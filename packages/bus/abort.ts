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

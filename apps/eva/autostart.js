import { validateName } from './runtime.js';

// A one-shot restore of the existing inventory, not a daemon or an allocator.
// Reuse exactly the same manager/locks/credentials/lifecycle as explicit starts.
export async function restoreConfiguredNodes(manager, { signal, onError = () => {} } = {}) {
  const report = (handle) => {
    if (signal?.aborted) return;
    try { onError(handle); } catch { /* Diagnostics must not break restoration. */ }
  };
  if (signal?.aborted) return;
  let inventory;
  try { inventory = await manager.status(undefined); }
  catch { report(); return; }
  if (signal?.aborted) return;
  const handles = new Set();
  for (const node of inventory?.nodes ?? []) {
    // A live stopped/failed record may reflect a newer explicit user action.
    // Only freshly discovered persisted identities are restoration candidates.
    if (node?.status !== 'not-started') continue;
    // Never allow an absent/invalid identity to mean "allocate a new node".
    try { validateName(node.handle); } catch { continue; }
    handles.add(node.handle);
  }
  const queue = [...handles];
  let cursor = 0;
  async function worker() {
    while (!signal?.aborted && cursor < queue.length) {
      const handle = queue[cursor++];
      try { await manager.start(undefined, handle, signal); }
      catch { report(handle); }
    }
  }
  // A slow node does not serialize every later restore; avoid spawn storms.
  await Promise.all(Array.from({ length: Math.min(2, queue.length) }, worker));
}

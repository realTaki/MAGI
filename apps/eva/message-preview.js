// Fixed-session, first-party observation only. Never open another profile's logs.
// Retain a cursor so polling folds only newly committed events, not old history.
export const unavailablePreview = () => ({ state: 'unavailable' });
export function safePreview(value) {
  if (value?.state === 'empty') return { state: 'empty' };
  if (value?.state === 'attachment') return { state: 'attachment' };
  if (value?.state === 'message' && typeof value.text === 'string') {
    const text = value.text.replace(/\s+/gu, ' ').trim();
    if (text) return { state: 'message', text: Array.from(text).slice(0, 240).join('') };
  }
  return unavailablePreview();
}
export function createMessagePreviewReader(ctx) {
  let previous;
  let pending;
  return function read(sessionId, signal) {
    if (!sessionId) return Promise.resolve(unavailablePreview());
    if (pending) return pending;
    pending = (async () => {
      let observation;
      try {
        observation = await ctx.sessionQuery.observeSession(sessionId, { signal, projectionMode: 'none' });
        const cursor = observation.cursor;
        // Unchanged observations need not materialize any event snapshot.
        if (previous?.sessionId === sessionId && previous.cursor === cursor) return previous.result;
        const events = observation.events;
        let result = previous?.sessionId === sessionId && previous.cursor <= cursor ? previous.result : { state: 'empty' };
        const start = previous?.sessionId === sessionId && previous.cursor <= cursor ? previous.cursor + 1 : 0;
        // Observation logs are ordered; locate the new suffix by seq rather
        // than assuming a log offset is an array index.
        let low = 0, high = events.length;
        while (low < high) {
          const middle = (low + high) >>> 1;
          if (events[middle].seq < start) low = middle + 1;
          else high = middle;
        }
        for (let index = low; index < events.length; index++) {
          const event = events[index];
          // An @user notice is visible when injected, not when its model-only
          // context is eventually consumed. Read only the raw display body.
          if (event.type === 'agent/inbox/spliced' && event.data?.target === 'next-step') {
            const inserted = event.data.inserted;
            if (Array.isArray(inserted)) for (const message of inserted) {
              const source = message?.source;
              if (source?.kind !== 'eva-user-dm' || typeof source.text !== 'string') continue;
              const preview = safePreview({ state: 'message', text: source.text });
              if (preview.state === 'message') result = preview;
            }
            continue;
          }
          let content;
          if (event.type === 'user/message' && event.data.source?.kind === 'user' && event.surfaceOp === 'append') content = event.data.content;
          else if (event.type === 'assistant/message' && event.surfaceOp === 'append') content = event.data.message?.content;
          else continue;
          const candidate = Array.isArray(content) ? content.filter((block) => block.type === 'text' && typeof block.text === 'string').map((block) => block.text).join(' ') : '';
          const preview = safePreview({ state: 'message', text: candidate });
          if (preview.state === 'message') result = preview;
          else if (content?.some((block) => !['text', 'reasoning', 'tool-call'].includes(block.type))) result = { state: 'attachment' };
        }
        previous = { sessionId, cursor, result };
        return result;
      } catch { return unavailablePreview(); }
      finally { observation?.[Symbol.dispose](); }
    })().finally(() => { pending = undefined; });
    return pending;
  };
}

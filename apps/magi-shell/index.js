// Shell owns parent navigation and preview; bot-mode owns its durable identity.
import { isEvaChildHome } from '@magi/bot-mode/parent';
import { createMessagePreviewReader } from './message-preview.js';

// Retain the pure startup operation export for existing integrations/tests.
export { initializeParentMain } from '@magi/bot-mode/parent';
export const name = 'magi-shell';
export const inject = ['appReady', 'profileContext', 'connection', 'magiBotMode', 'sessionQuery'];
export const PARENT_MAIN_PATH = '/api/eva/parent-main';
const headers = { 'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer' };

export async function apply(ctx) {
  let bot;
  const readPreview = createMessagePreviewReader(ctx);
  let disposed = false;
  let validParent = false;
  // Child suppression and sanitized read-only route behavior remain unchanged.
  try {
    if (await isEvaChildHome(ctx.profileContext.home)) return;
    validParent = true;
  } catch { /* Publish only the fixed unavailable response below. */ }
  ctx.effect(() => {
    const disposeRoute = ctx.connection.fetch.register({ path: PARENT_MAIN_PATH, methods: ['GET'], requestBody: 'buffered',
      async fetch(request) {
        const current = bot;
        const preview = current && !disposed ? await readPreview(current.mainSessionId, request?.signal) : undefined;
        return current && !disposed
          ? Response.json({ mode: 'parent', bot: { ...current, preview } }, { headers })
          : Response.json({ code: 'parent-main-unavailable', error: 'The parent Bot is not ready.' }, { status: 503, headers });
      },
    });
    const cancelReady = validParent ? ctx.appReady.onReady(() =>
      ctx.magiBotMode.initialize().then((value) => {
        if (!disposed && value) bot = {
          handle: 'dsh-main', kind: 'parent', status: 'running', workspace: value.workspace,
          mainSessionId: value.sessionId,
        };
      }).catch(() => { /* Readiness stays unavailable; never leak raw paths/errors. */ })) : undefined;
    return () => { disposed = true; bot = undefined; cancelReady?.(); return disposeRoute(); };
  });
}

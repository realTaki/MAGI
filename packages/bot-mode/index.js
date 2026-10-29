// One Host owner for fixed Bot identity. Agent capabilities are declared separately
// by this package's preset resource; no process launcher or global tool providers.
import { realpath } from 'node:fs/promises';
import { join } from 'node:path';
import { initializeParentMain, isEvaChildHome } from './parent.js';
import { ensureMainSession, attachMainSessionWorkspace, ensureMainSessionTitle } from './main-session.js';

export const name = 'magi-bot-mode';
export const inject = ['profileContext', 'workspaceRegistry', 'sessionController', 'sessionPersistence', 'sessionQuery', 'workingDirectory'];

export async function apply(ctx, config = {}) {
  if (!config || typeof config !== 'object' || Array.isArray(config)) throw new Error('Invalid Bot mode identity configuration.');
  const { role = 'parent', workspace, profile } = config;
  if (Object.keys(config).some(key => !['role', 'workspace', 'profile'].includes(key)) ||
      !['parent', 'eva-child'].includes(role) ||
      (role === 'parent' && (workspace !== undefined || profile !== undefined)) ||
      (role === 'eva-child' && (typeof workspace !== 'string' || typeof profile !== 'string' || !/^[a-z][a-z0-9-]{0,31}$/.test(profile)))) {
    throw new Error('Invalid Bot mode identity configuration.');
  }
  let disposed = false, current, pending;
  ctx.effect(() => () => { disposed = true; current = undefined; });
  if (role === 'parent') {
    // Match Shell's fail-closed role guard, including profiles changed in a child.
    // Invalid paths remain unavailable; activation itself never creates identity.
    try { if (await isEvaChildHome(ctx.profileContext.home)) return; } catch { /* initialize rejects */ }
  }
  if (disposed) return;
  if (ctx.get('magiBotMode') || ctx.get('magiMainSession')) throw new Error('Only one Bot mode identity owner may be mounted in a Host.');

  async function initialize() {
    if (disposed) throw new Error('Bot mode is disposed.');
    if (current) return current;
    if (pending) return pending;
    pending = (async () => {
      let value;
      if (role === 'parent') {
        const bot = await initializeParentMain(ctx);
        if (!bot) return undefined;
        value = { sessionId: bot.mainSessionId, workspace: bot.workspace };
      } else {
        const home = await realpath(ctx.profileContext.home);
        if (home !== ctx.profileContext.home || workspace !== home || ctx.profileContext.name !== profile ||
            await realpath(ctx.profileContext.dir) !== ctx.profileContext.dir) {
          throw new Error('EVA Bot mode identity must match its canonical Bot home and profile.');
        }
        await ctx.workspaceRegistry.create(workspace, `EVA · ${profile}`);
        const main = await ensureMainSession(ctx, { workspace, profile, stateDir: join(home, 'eva') });
        await attachMainSessionWorkspace(ctx, main.sessionId);
        await ensureMainSessionTitle(ctx, main.sessionId, profile);
        value = { sessionId: main.sessionId, workspace };
      }
      // Never resurrect a service after unload while attachment/flush was pending.
      if (disposed) return undefined;
      current = Object.freeze(value);
      return current;
    })().finally(() => { pending = undefined; });
    return pending;
  }

  // Preserve DM's optional read-only ABI; startup mutation is a separate service.
  ctx.provide('magiMainSession', Object.freeze({ current: () => disposed ? undefined : current }));
  ctx.provide('magiBotMode', Object.freeze({ initialize }));
}

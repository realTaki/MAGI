import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { openMemoryStore } from './store.js';
import { memoryTools } from './tools.js';

export const name = 'magi-memory';
export const inject = ['profileContext', 'systemPrompt', 'tools'];

// One Host contribution reaches every Agent scope, without changing presets or
// rewriting model requests. Native runtime context owns logging and compaction.
export function apply(ctx, config = {}) {
  if (!config || typeof config !== 'object' || Array.isArray(config) || Object.keys(config).length) {
    throw new Error('Memory has no configurable storage path; it belongs to this Bot home.');
  }
  if (ctx.get('magiMemory')) throw new Error('Only one MAGI memory root may be mounted in a Host.');
  const home = ctx.profileContext.home;
  const store = openMemoryStore(home);
  ctx.effect(() => () => store.close());
  ctx.provide('magiMemory', Object.freeze({ home, version: 1 }));
  ctx.systemPrompt.context({
    name: 'magi:memory', order: 850, required: true, interpolate: false,
    // Read committed SQLite state, so other profiles in this same Bot home and
    // concurrent Sessions see committed changes on their next admitted request.
    text: () => store.render(),
  });
  for (const tool of memoryTools(store)) ctx.tools.register(tool);
  // Discovery is optional; service reload must not interrupt memory or its tools.
  ctx.inject(['skills'], scoped => {
    const directory = new URL('./skills/bot-memory/', import.meta.url);
    const document = new URL('SKILL.md', directory);
    scoped.effect(() => scoped.skills.register({
      name: 'magi-bot-memory',
      description: "Remember, reconcile, archive, or remove this Bot's reusable facts and preferences without duplicates or stale writes.",
      source: 'runtime',
      path: fileURLToPath(document),
      resourceBase: { kind: 'directory', path: fileURLToPath(directory) },
      content: readFileSync(document, 'utf8'),
    }));
  });
}

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// Native discovery is optional; losing it must not unload the node manager.
// The injected scope owns registration and teardown, including service reloads.
export function registerEvaSkill(ctx) {
  ctx.inject(['skills'], scoped => {
    const directory = new URL('./skills/eva-nodes/', import.meta.url);
    const document = new URL('SKILL.md', directory);
    scoped.effect(() => scoped.skills.register({
      name: 'magi-eva-nodes',
      description: 'Create, start, inspect or stop local EVA Bots, distinguish reuse from allocation, and diagnose unavailable nodes safely.',
      source: 'runtime',
      path: fileURLToPath(document),
      resourceBase: { kind: 'directory', path: fileURLToPath(directory) },
      content: readFileSync(document, 'utf8'),
    }));
  });
}

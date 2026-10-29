// Bundle patch expressions are flattened into the Host: only this module's URL
// reliably locates a nested bundled dependency's declaration. Resolve the native
// Include implementation through DSH's installation anchor, not workspace links.
// Keep YAML one directory below this package: native source-linked resolution
// takes dirname of Include's directory base before identifying the owning peer.
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

export const name = 'magi-bot-mode-preset';
export const inject = ['profileContext'];

export function apply(ctx) {
  const include = createRequire(ctx.profileContext.installAnchor)('@deepseek-ai/cordis-plugin-include');
  ctx.plugin(include.default ?? include, { path: fileURLToPath(new URL('./presets/bot-preset.yml', import.meta.url)) });
}

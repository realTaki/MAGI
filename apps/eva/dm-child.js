// Mounted explicitly in the child startup overlay; never starts another Host.
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { dmInject, installChildDm } from './dm-plugin.js';
export const name = 'eva-dm-child';
export const inject = dmInject;
export async function apply(ctx, config) {
  // Absolute overlay entries are not linked profile packages; bare DSH imports
  // there do not receive the runtime fallback used by the parent's bundle.
  const entry = createRequire(ctx.profileContext.installAnchor).resolve('@deepseek-ai/dsh-tools');
  const { defineTool } = await import(pathToFileURL(entry).href);
  installChildDm(ctx, config, defineTool);
}

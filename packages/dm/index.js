// Reusable Host plugin. Integrations own Bot identity, transport admission and
// the fixed main Session; this package owns DM tools, durability and delivery.
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { dmInject, installParentDm, installChildDm } from './dm-plugin.js';

export const name = 'magi-dm';
export const inject = [...dmInject, 'magiDmTransport'];

export async function apply(ctx) {
  const transport = ctx.magiDmTransport;
  if (!transport || !['broker', 'child'].includes(transport.kind)) {
    throw new Error('DM requires a supported Host-owned transport adapter.');
  }
  if (transport.kind === 'child' && (!transport.channel || typeof transport.channel.send !== 'function')) {
    throw new Error('DM child requires an explicit managed parent IPC channel.');
  }
  // Resolve through the native profile anchor, including absolute child entries.
  // Never depend on a source checkout or on the current working directory.
  const entry = createRequire(ctx.profileContext.installAnchor).resolve('@deepseek-ai/dsh-tools');
  const { defineTool } = await import(pathToFileURL(entry).href);
  const installation = transport.kind === 'broker'
    ? installParentDm(ctx, transport, defineTool)
    : installChildDm(ctx, { profile: transport.handle, workspace: transport.workspace }, defineTool, transport.channel);
  ctx.provide('magiDm', installation);
  if (transport.kind === 'broker' && typeof transport.bindBroker === 'function') {
    ctx.effect(() => transport.bindBroker(installation.attach));
  }
}

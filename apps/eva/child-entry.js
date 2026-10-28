// One Loader source owns @local/eva's Client metadata in each child Host.
// Keep lifecycle and DM contributions in that same Host-local Cordis scope.
import * as lifecycle from './child.js';
import * as dm from './dm-child.js';

export const name = 'eva-child-host';
export const inject = [...new Set([...lifecycle.inject, ...dm.inject])];

export async function apply(ctx, config) {
  lifecycle.apply(ctx, config);
  await dm.apply(ctx, { profile: config.profile });
}

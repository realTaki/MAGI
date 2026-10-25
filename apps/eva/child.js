// Small startup probe mounted only in the additional Host, never in the desktop.
import { realpath } from 'node:fs/promises';

export const name = 'eva-child';
export const inject = ['appReady', 'appExit', 'workspaceRegistry', 'webServer', 'profileContext', 'agentPresets'];

export function apply(ctx, config) {
  let disposed = false;
  ctx.effect(() => {
    const cancel = ctx.appReady.onReady(() => {
      void (async () => {
        const workspace = await realpath(config.workspace);
        const home = await realpath(ctx.profileContext.home);
        const cwd = await realpath(process.cwd());
        if (home !== ctx.profileContext.home || cwd !== home) throw new Error('eva child cwd differs from its DSH_HOME.');
        if (ctx.profileContext.name !== config.profile) throw new Error('eva child profile mismatch.');
        if (await realpath(ctx.profileContext.dir) !== ctx.profileContext.dir) throw new Error('eva child profile must not be redirected by a symlink.');
        const project = await ctx.workspaceRegistry.create(workspace, `EVA · ${config.profile}`);
        const preset = await ctx.agentPresets.resolve();
        if (preset.broken) throw new Error('eva child default agent preset is unavailable.');
        if (!disposed && process.connected) process.send({
          type: 'eva-ready', pid: process.pid, parentPid: process.ppid,
          profile: ctx.profileContext.name, profileDir: ctx.profileContext.dir,
          workspace, workspaceId: project.id, home, cwd, defaultAgentPreset: preset.id,
          origin: ctx.webServer.getOrigin(),
        });
      })().catch((error) => {
        console.error(`eva child initialization failed: ${error.message}`);
        void ctx.appExit(1);
      });
    });
    return () => { disposed = true; cancel(); };
  });
}

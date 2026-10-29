// Small startup probe mounted only in the additional Host, never in the desktop.
import { realpath } from 'node:fs/promises';
import { registerChildView, registerEmbeddedLogin } from './view.js';
import { credentialStorePath } from './credentials.js';
import { createMessagePreviewReader } from './message-preview.js';

export const name = 'eva-child';
export const inject = ['appReady', 'appExit', 'workspaceRegistry', 'webServer', 'profileContext', 'agentPresets', 'connection', 'magiBotMode', 'sessionQuery', 'credentials', 'llm'];

export function apply(ctx, config) {
  let disposed = false;
  let mainSessionId;
  // bot-mode owns fixed identity; EVA supplies process transport and startup probes.
  ctx.provide('magiDmTransport', {
    kind: 'child', workspace: config.workspace, handle: config.profile, channel: process,
  });
  registerChildView(ctx, () => mainSessionId);
  registerEmbeddedLogin(ctx);
  const readPreview = createMessagePreviewReader(ctx);
  ctx.effect(() => {
    let polling = false;
    const abort = new AbortController();
    const poll = async () => {
      if (disposed || polling || !mainSessionId || !process.connected) return;
      polling = true;
      try {
        const preview = await readPreview(mainSessionId, abort.signal);
        if (!disposed && process.connected) process.send({ type: 'eva-preview', mainSessionId, preview }, () => {});
      } finally { polling = false; }
    };
    const timer = setInterval(() => { void poll(); }, 3000);
    timer.unref();
    const cancel = () => { clearInterval(timer); abort.abort(); };
    return cancel;
  });
  ctx.effect(() => {
    const cancel = ctx.appReady.onReady(() => {
      void (async () => {
        const workspace = await realpath(config.workspace);
        const home = await realpath(ctx.profileContext.home);
        const cwd = await realpath(process.cwd());
        if (home !== ctx.profileContext.home || cwd !== home || workspace !== home) throw new Error('eva child workspace and cwd must equal its DSH_HOME.');
        if (ctx.profileContext.name !== config.profile) throw new Error('eva child profile mismatch.');
        if (await realpath(ctx.profileContext.dir) !== ctx.profileContext.dir) throw new Error('eva child profile must not be redirected by a symlink.');
        const credentialsShared = config.credentialsPath !== undefined;
        if (credentialsShared && await credentialStorePath(ctx) !== config.credentialsPath) {
          throw new Error('eva child credential store does not match the parent sharing configuration.');
        }
        const codexAvailable = ctx.llm.listProviders().some(({ id }) => id === 'openai-codex')
          && (await ctx.llm.listModels('openai-codex')).length > 0;
        if (!codexAvailable) throw new Error('eva child native Codex provider is unavailable.');
        const preset = await ctx.agentPresets.resolve();
        if (preset.broken) throw new Error('eva child default agent preset is unavailable.');
        const main = await ctx.magiBotMode.initialize();
        // Bot identity is published only after durable attachment and title.
        if (disposed || !main) return;
        if (main.workspace !== workspace) throw new Error('eva child Bot mode workspace mismatch.');
        const project = await ctx.workspaceRegistry.create(workspace, `EVA · ${config.profile}`);
        if (disposed) return;
        mainSessionId = main.sessionId;
        const launchUrl = new URL(ctx.connection.authenticatedUrl(ctx.webServer.getOrigin()));
        launchUrl.hash = 'eva-main';
        if (!disposed && process.connected) process.send({
          type: 'eva-ready', pid: process.pid, parentPid: process.ppid,
          profile: ctx.profileContext.name, profileDir: ctx.profileContext.dir,
          workspace, workspaceId: project.id, home, cwd, defaultAgentPreset: preset.id,
          origin: ctx.webServer.getOrigin(), mainSessionId, launchUrl: launchUrl.href, credentialsShared, codexAvailable,
        });
      })().catch((error) => {
        console.error(`eva child initialization failed: ${error.message}`);
        void ctx.appExit(1);
      });
    });
    return () => { disposed = true; cancel(); };
  });
}

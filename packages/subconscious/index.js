import { z } from 'zod';
import { createController, initialState } from './controller.js';
import { MEMORY_TOOLS, preset } from './policy.js';

export const name = 'magi-subconscious';
export const inject = ['agents', 'agentPresets', 'storageDomain', 'sessionPersistence', 'sessions',
  'sessionQuery', 'timer', 'tools', 'approval', 'magiMainSession', 'magiMemory'];
export const Config = z.object({
  enabled: z.boolean().default(false),
  runId: z.string().trim().max(100).default(''),
  recurring: z.boolean().default(false),
  dailyTurnLimit: z.number().int().min(1).max(12).default(6),
  dailyStepLimit: z.number().int().min(1).max(24).default(12),
  maxTurns: z.number().int().min(1).max(3).default(3),
  maxSteps: z.number().int().min(1).max(12).default(6),
  maxTokens: z.number().int().min(128).max(2048).default(1200),
  cooldownMs: z.number().int().min(30_000).max(3_600_000).default(60_000),
  heartbeatMinutes: z.number().int().min(5).max(1440).default(30),
  maxRunMs: z.number().int().min(0).max(86_400_000).default(3_600_000),
  provider: z.string().min(1).optional(),
  model: z.string().min(1).optional(),
}).strict().refine(value => !value.enabled || value.runId.length > 0,
  { message: 'Enabling requires a new, human-chosen runId.' });

const State = z.object({
  mainSessionId: z.string().nullable(), workspace: z.string().nullable(),
  sessionId: z.string().nullable(), pendingId: z.string().nullable(), runId: z.string().nullable(),
  paused: z.boolean(), reason: z.string(), turns: z.number().int().nonnegative(), steps: z.number().int().nonnegative(),
  startedAt: z.number().nonnegative(), cursor: z.number().int().min(-1),
  budgetDay: z.string(), dailyTurns: z.number().int().nonnegative(), dailySteps: z.number().int().nonnegative(),
  evidence: z.object({ through: z.number().int(), text: z.string().max(8000) }).nullable(),
  proposals: z.array(z.object({ text: z.string().max(2000), selfSessionId: z.string().nullable(),
    through: z.number().int(), at: z.number() })).max(5),
});
const emptyParameters = { type: 'object', properties: {}, additionalProperties: false };
function tool(name, description, parameters, execute) {
  return { name, description, parameters, isConcurrencySafe: () => false,
    output: { schema: { type: 'object', additionalProperties: true },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
    execute };
}

export async function apply(ctx, input = {}) {
  const config = Config.parse(input);
  if (ctx.get('magiSubconscious')) throw new Error('Only one subconscious root may own this Bot.');
  const unregisterPreset = await ctx.agentPresets.register(preset);
  ctx.effect(() => unregisterPreset);
  const domain = await ctx.storageDomain.open({ name: 'magi-subconscious', version: 1,
    layout: 'single', global: { schema: State, initial: initialState }, tables: {} });
  let controller;
  const installed = new WeakSet(), cleanupScopes = new Set();
  function installScope(agentCtx, agent) {
    if (installed.has(agent)) return;
    installed.add(agent);
    const cleanup = [];
    const add = dispose => { cleanup.push(dispose); return dispose; };
    // These masks affect only this Agent. Even globally installed delegation,
    // schedules/config tools and destructive memory cannot leak into the mode.
    add(agentCtx.tools.restrict({ allow: MEMORY_TOOLS }));
    const localTools = [
      tool('subconscious_status', 'Read this bounded run’s remaining state; cannot enable or replenish it.', emptyParameters,
        async () => controller.status()),
      tool('subconscious_pause', 'Stop this reflection batch when no useful substantive work remains. Cannot restart it.', emptyParameters,
        async (_args, exec) => {
          exec.signal.throwIfAborted();
          await controller.requestPause('complete');
          return { paused: true };
        }),
      tool('subconscious_propose', 'Record a short evidence-based improvement for human review; log it into live MAIN without waking MAIN. Does not execute changes.',
        { type: 'object', properties: { text: { type: 'string', minLength: 1, maxLength: 2000 } }, required: ['text'], additionalProperties: false },
        async (args, exec) => {
          exec.signal.throwIfAborted();
          const text = z.string().trim().min(1).max(2000).parse(args.text);
          return controller.propose(text);
        }),
    ];
    for (const definition of localTools) add(agentCtx.tools.register(definition));
    const allowed = new Set([...MEMORY_TOOLS, ...localTools.map(def => def.name)]);
    add(agentCtx.tools.guard(exec => {
      if (exec.agent !== agent || !allowed.has(exec.name)) return 'Background reflection is restricted to memory and review tools.';
      if (controller.status().paused) return 'Background reflection is paused.';
    }));
    const release = () => { for (const dispose of cleanup.splice(0).reverse()) dispose(); cleanupScopes.delete(release); };
    cleanupScopes.add(release);
    agentCtx.effect(() => release);
  }
  controller = createController(ctx, config, domain, installScope);
  ctx.provide('magiSubconscious', Object.freeze({ status: controller.status, pause: controller.requestPause }));
  // A cold resume through another official entry point must not bypass the
  // paused/budget admission gate or acquire global capabilities.
  ctx.on('agent/created', ({ agent }) => {
    if (controller.matches(agent)) installScope(agent.ctx, agent);
  });
  ctx.on('agent/pre-step', (payload, next) => controller.preStep(payload, next));
  ctx.on('workspace/session-stop', ({ sessionId }) => {
    if (controller.matches({ id: sessionId })) return controller.requestPause('workspace-stop');
  });
  ctx.effect(() => async () => {
    try { await controller.dispose(); }
    finally { for (const release of cleanupScopes) release(); await domain.close(); }
  });
  try { await controller.activate(); }
  catch (error) { await controller.requestPause(`error: ${error.message}`); ctx.logger.warn(error.message); }
}

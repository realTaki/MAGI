import { z } from 'zod';
// This optional peer is available in properly installed native profiles, but
// may be absent from an external source-linked subtree. Keep that layout safe
// with the original real Zod contract, never a manufactured native graph.
let Schema;
try {
  Schema = (await import('@deepseek-ai/schemastery')).default;
  if (!Schema) throw new TypeError('Schemastery must export its native Schema API.');
} catch (error) {
  if (!['ERR_MODULE_NOT_FOUND', 'MODULE_NOT_FOUND'].includes(error.code) ||
      !/^Cannot find (?:package|module) ['"]@deepseek-ai\/schemastery['"](?: imported from|\s|$)/.test(error.message)) throw error;
}
import { createController, initialState } from './controller.js';
import { MEMORY_TOOLS, preset } from './policy.js';

export const name = 'magi-subconscious';
export const inject = ['agents', 'agentPresets', 'storageDomain', 'sessionPersistence', 'sessions',
  'sessionQuery', 'timer', 'tools', 'approval', 'magiMainSession', 'magiMemory'];
const RuntimeConfig = z.object({
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

// Native forms/Inspect consume Schemastery's graph, not Zod or JSON Schema.
// Keep the strict validator at the exported boundary as well as direct apply.
export const nativeConfigAvailable = Schema !== undefined;
export const Config = nativeConfigAvailable ? Schema.transform(Schema.object({
  enabled: Schema.boolean().default(false),
  runId: Schema.string().max(100).default(''),
  recurring: Schema.boolean().default(false),
  dailyTurnLimit: Schema.number().step(1).min(1).max(12).default(6),
  dailyStepLimit: Schema.number().step(1).min(1).max(24).default(12),
  maxTurns: Schema.number().step(1).min(1).max(3).default(3),
  maxSteps: Schema.number().step(1).min(1).max(12).default(6),
  maxTokens: Schema.number().step(1).min(128).max(2048).default(1200),
  cooldownMs: Schema.number().step(1).min(30_000).max(3_600_000).default(60_000),
  heartbeatMinutes: Schema.number().step(1).min(5).max(1440).default(30),
  maxRunMs: Schema.number().step(1).min(0).max(86_400_000).default(3_600_000),
  provider: Schema.string().min(1),
  model: Schema.string().min(1),
}), value => RuntimeConfig.parse(value)) : RuntimeConfig;

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
  const config = RuntimeConfig.parse(input);
  if (ctx.get('magiSubconscious')) throw new Error('Only one subconscious root may own this Bot.');
  const unregisterPreset = await ctx.agentPresets.register(preset);
  ctx.effect(() => unregisterPreset);
  const domain = await ctx.storageDomain.open({ name: 'subconscious', version: 1,
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
  const inspect = ctx.get('cordisInspect');
  if (inspect) ctx.effect(() => inspect.register({
    manifest: {
      id: 'MagiSubconscious',
      description: 'Read-only subconscious lifecycle and configured/consumed budgets; no Session messages or proposals.',
      methods: [{ name: 'status', description: 'Read current lifecycle and budgets without waking or changing any Session.',
        inputSchema: emptyParameters, outputSchema: { type: 'object', additionalProperties: true } }],
    },
    async query(method) {
      if (method !== 'status') throw new Error('Unknown subconscious inspect method.');
      const state = controller.status();
      const reasons = new Set(['not-enabled', 'active', 'waiting', 'complete', 'paused',
        'disposed', 'workspace-stop', 'run-timeout', 'batch-timeout', 'budget-exhausted']);
      return {
        nativeConfigAvailable,
        enabled: config.enabled, recurring: config.recurring, runId: config.runId,
        paused: state.paused, reason: reasons.has(state.reason) ? state.reason : 'error',
        running: state.running, waiting: state.waiting, sessionId: state.sessionId,
        turns: state.turns, steps: state.steps, budgetDay: state.budgetDay,
        dailyTurns: state.dailyTurns, dailySteps: state.dailySteps,
        maxTurns: config.maxTurns, maxSteps: config.maxSteps, maxTokens: config.maxTokens,
        dailyTurnLimit: config.dailyTurnLimit, dailyStepLimit: config.dailyStepLimit,
        heartbeatMinutes: config.heartbeatMinutes, cooldownMs: config.cooldownMs, maxRunMs: config.maxRunMs,
      };
    },
  }));
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

import { defineTool } from '@deepseek-ai/dsh-tools';
import { EvaRuntime, desktopRuntime, validateName } from './runtime.js';

export const name = 'eva';
export const inject = ['tools', 'workingDirectory', 'profileContext', 'approval'];

export function apply(ctx) {
  const manager = new EvaRuntime(desktopRuntime(ctx.profileContext.installAnchor));
  ctx.effect(() => () => manager.dispose());
  ctx.tools.register(defineTool({
    name: 'eva',
    description: 'Launch, inspect, or stop desktop-free DSH Hosts. Each handle keeps its profile, state, and workspace under ~/.dsh-<handle>; workspace is ~/.dsh-<handle>/workspace, process cwd is its DSH_HOME, and no parent credentials are copied. Registering a workspace does not create a Session. New Sessions default to the Creator (cordis) preset unless the user saved another default; existing Sessions are unchanged. Creator does not bypass tool approvals. Existing registered legacy workspaces without session history migrate on start. Start without a handle scans only direct home directory entries and atomically allocates eva-000, eva-001, etc. Each such call creates a new node; reuse its returned handle for status/stop or idempotent start. Status without a handle lists this plugin instance\'s managed nodes; stop requires a handle. Binds loopback without opening a window. Start/stop require one-shot approval. Nodes stop when the plugin or parent Host exits. Not an OS security boundary.',
    parameters: {
      action: { type: 'string', enum: ['start', 'status', 'stop'], required: true, description: 'Start a DSH, inspect managed nodes, or stop a node.' },
      handle: { type: 'string', description: 'Optional node handle: lowercase letters, digits and hyphens; starts with a letter, maximum 32 characters. Start omitting it allocates eva-000 upward from directory occupancy. Stop requires it.' },
      name: { type: 'string', description: 'Deprecated alias for handle. Do not supply a different handle and name together.' },
    },
    output: {
      schema: { type: 'json' },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value, null, 2) }],
    },
    isConcurrencySafe: (args) => args.action === 'status',
    async execute(args, exec) {
      if (!exec.agent) throw new Error('eva requires an agent Session.');
      exec.signal.throwIfAborted();
      if (args.handle !== undefined && args.name !== undefined && args.handle !== args.name) throw new Error('Conflicting EVA handle and name.');
      const handle = args.handle ?? args.name;
      if (handle !== undefined) validateName(handle);
      if (args.action === 'stop' && handle === undefined) throw new Error('EVA stop requires a handle.');
      const base = await ctx.workingDirectory.ensure(exec.agent, exec.signal);
      if (args.action === 'status') return manager.status(base, handle);
      const label = handle ?? 'eva-NNN（扫描目录自动分配）';
      const paths = handle === undefined
        ? { home: `${manager.homeRoot}/.dsh-eva-NNN`, workspace: `${manager.homeRoot}/.dsh-eva-NNN/workspace` }
        : manager.paths(handle);
      const reason = args.action === 'start'
        ? `启动独立 DSH 节点 ${label}：数据根及 cwd ${paths.home}，注册工作区 ${paths.workspace}，独立 profile，新对话默认 Creator（尊重用户已保存默认，不改变已有对话或权限）；无桌面本地服务，不复制主实例凭证。旧版已注册且没有会话历史的工作区会迁入节点根目录，校验成功后删除旧位置。未指定 handle 时，每次启动创建一个新编号节点。`
        : `停止 eva 管理的 DSH 节点 ${handle}；该节点正在进行的任务可能被中断。`;
      const outcome = await ctx.approval.request({
        agent: exec.agent, toolName: 'eva', callId: exec.callId,
        reason, displayReason: { zh: reason, en: `${args.action} EVA node ${label}; data and cwd: ${paths.home}; registered workspace: ${paths.workspace}. A separate DSH process, without copied credentials. New Sessions default to Creator (cordis), respecting saved user defaults; existing Sessions and permissions are unchanged. On start, a registered legacy workspace without session history is relocated under the node home and the verified old source is removed. An unnamed start allocates a new node; stopping may interrupt work.` },
        signal: exec.signal,
      });
      if (outcome !== 'allowed-once') throw new Error(`eva ${args.action} was not approved (${outcome}); no action was performed.`);
      exec.signal.throwIfAborted();
      return args.action === 'start' ? manager.start(base, handle, exec.signal) : manager.stop(base, handle);
    },
  }));
}

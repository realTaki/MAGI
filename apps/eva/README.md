# EVA

DSH Host + Web Client 插件，提供模型工具 `eva`、节点清单与会话嵌入支持，管理额外的无桌面窗口 DSH 进程。节点入口统一放在 MAGI 工作台，不再向原生侧栏插入 EVA-NNN 条目。目标环境为 DeepSeek Harness Desktop 0.2.1-rc.3 / macOS。当前源码插件版本 0.7.0；本次独立 DM 重构尚未应用到运行实例。

## MAGI 重构起点

本目录已用 DSH Host 插件替换 MAGI 旧的 EVA launcher，作为逐步改造成 DSH 版本的起点。其他旧模块和根级构建流程尚未改造，不能据此认为整个 MAGI 已兼容 DSH。背景、验证边界和后续工作见 [交接文档](<../../DSH-HANDOFF.md>)。

## 共享 bot-mode（源码完成，尚未部署）

EVA 安装并捆绑独立 [@magi/bot-mode](<../../packages/bot-mode/README.md>)。子 overlay v7 通过已安装包的绝对入口挂载固定身份能力，并包含包拥有的 `bot-mode` 原生 Agent 声明及同源 `eva` 兼容声明；选中 Shell/独立 bundle 的父身份和预设根都禁用。bot-mode 拥有固定会话恢复、持久化、workspace 附着、子 Bot 标题及通用工具/上下文/隔离配置；EVA 只保留进程、home/profile/端口、IPC、凭据、原生 Codex 路由、启动检查与嵌入。

子端仅将部署默认候选改为 `bot-mode`，不改已保存默认值或现有会话选择；保留 `eva` ID 以恢复已有会话。旧 v1 子 workspace 升级仍保留同一 Session；父身份仍执行严格 v2 策略。Shell 消费同一父身份和通用预设，但不改父默认值。DM/memory 仍独立；状态路径、历史与权限不迁移。未重载、重启或部署运行实例。

## 潜意识源码接线（默认暂停，尚未部署）

EVA 独立捆绑 [@magi/subconscious](<../../packages/subconscious/README.md>)；父端 `magi-subconscious` 与子端 `magi-subconscious-child` 均为 `config: {}`，不设置启用开关。子端通过安装包绝对入口挂权威根并排除所选 bundle 的父根；v7 启动 hash 纳入入口，旧 immutable overlay、profile 和运行进程不重写。该组件有独立根 Session，不复用 MAIN/subagent，不合并 memory/DM，不归 Shell 所有。显式启用后的目标是低流量单条串行反思，小预算、冷却及无新输入跳过，不是 24/7 模型消耗。此改动未安装、重载、启用或发送模型请求。

## EVA 记忆归属（源码修复，部署需维护许可）

EVA 的 `magi-memory` 根 ID 不变，入口改为 EVA 拥有的 `@local/eva/memory`。它只重导出独立 memory 实现，不合并生命周期或复制存储逻辑；随 EVA 导出独立的「Bot 记忆」中英文说明和原有原创图标。打包后的实现从 EVA 自己的物理依赖解析，不再让 profile 额外安装的 `@magi/memory` 源码链接抢占父根。子 overlay 对所选父记忆根只按 ID 禁用，仍只挂一个显式 memory 子根。

[原生归属探针](<test/memory-ownership-native.js>)使用当前安装的真实 Loader / app-boot / 模块解析器和一次性物理打包 fixture；覆盖 standalone 与 EVA 同 ID 的 last-wins、删除 direct dependency 前后、父子唯一根、物理实现身份、Config 包归属、完整显示元数据以及同 home 的 SQLite 事实 ID 保留。不启动 Host/Bot、不修改运行 profile。运行示例：

```sh
ELECTRON_RUN_AS_NODE=1 /Applications/MAGI.app/Contents/MacOS/MAGI --expose-internals apps/eva/test/memory-ownership-native.js /absolute/current/dsh/package.json
```

实际修复顺序（此处仅说明，不执行）：取得用户维护许可 → 将已确认源码修复合为一个 EVA 归档 → 原生 Plugin Manager 更新 EVA → 官方 `remove_bundle @magi/memory` 移除冗余独立安装 → 核验只有一个记忆服务、原 home/事实 ID/数量不变、额外 direct dependency/卡片消失，刷新原页面并人工验收。数据位于 `<profileContext.home>/memory/memories.sqlite`，不在安装包目录；不删除、迁移或复制活跃 WAL 数据声称备份。

**更新会执行 EVA dispose → manager.dispose → runtime.stopNode，停止所有受管理的运行子节点，可能中断进行中的任务；新插件自动恢复原 persistent IDs。不是仅记忆瞬断。** 未取得维护许可不得更新、重启、停节点或新建 Bot；本源码修改不表示已部署。

## 安装与更新

源码目录链接安装前，先在仓库根用 MAGI 自带 Node/npm 从根锁文件安装 EVA/DM/memory/bot-mode/subconscious workspace 及其普通依赖（包括 zod），保留 `--ignore-scripts`；例如 `npm ci --workspace @local/eva --ignore-scripts --omit=dev`。只有缓存已备齐时再加 `--offline`。不在运行 profile 中执行包管理器。原生目录链接本身不会代装依赖。

分发到没有仓库源码的机器时，使用[专用打包脚本](<../../scripts/pack-dsh.mjs>)（MAGI 自带 Node 执行 `scripts/pack-dsh.mjs --app eva --out dist`）。它把独立 DM、memory、bot-mode 和 subconscious 包实体纳入 EVA 归档；普通 `npm pack` 可能静默漏掉被 workspace 提升的符号链接依赖。安装归档仍使用原生 Plugin Manager。

1. 左侧「插件」→「添加插件」。
2. 输入本插件目录的绝对路径：`/Users/shichuan/.dsh/workspace/MAGI/apps/eva`。
3. 点击「安装」，完成后「立即启用」。

也可在新的 Creator 会话用 `plugin_manager install_bundle` 安装本目录。入口为「插件」→「添加插件」右侧箭头→「让 Agent 创建插件」；不能将已开始的会话切换成 Creator。

不要手动修改主实例 profile，不要在 profile 内运行包管理器。更新已链接安装的 Host 源码后，可通过 `plugin_manager` 对 `@local/eva` 再次执行 `set_bundle`、`enabled:true`：支持 HMR 的 profile 即使选择未变也会比较链接目标源码并协调重新加载；`changed:false` 仅表示配置文件未变，应以 `application`、warnings 和运行能力判断是否生效。重新加载 EVA 仍可能停止其管理的子节点，操作前确认状态。仅在返回 `restart-required` 时按提示重启主实例。仍需通过实际工具调用确认新代码已经加载，不能把磁盘更新或显示的新版本号等同于当前进程已更新。本插件无需构建或系统 Node/pnpm/dsh 命令。

仅更新 Web Client 时，先确认当前 Host 已重新读取新的 Client 文件并发布新 revision，再刷新现有页面验证；本地链接或刷新页面本身不保证服务端内存里的旧 bundle 已更新。不要随意为此关闭/重新启用 EVA Host，卸载 Host 会停止其管理的子节点。需要重新切换 bundle 才能更新 Client 时，应先告知影响并确认节点状态；未验证 watcher 与实际页面前不承诺自动热更新。

## 启动时恢复已有 EVA

父 Host 的 EVA 插件启用后，等待原生 `appReady` 再从现有持久化节点清单恢复离线 EVA，不需要用户逐个手动启动。这里的「已配置」指清单能安全识别的已有节点记录，不是遍历任意目录执行程序；复用原 handle、数据根和固定主会话，不调用无 handle 的自动编号分配，也不因为清单为空而创建新 Bot。

恢复只在本次插件启用时进行一轮，单个节点失败不影响其他节点；不会持续自动重启崩溃或被用户停止的节点。插件卸载/父 Host 退出时仍停止其管理的子进程，尚未完成的恢复也被取消。已有外部 owner lock 不会被接管、删除或按 PID 杀进程。子 Host 不注册启动器，也不会递归恢复下一层 EVA。

本次自动恢复改动后，EVA + MAGI Shell 联合回归 **334/334 通过**，包含 13 项就绪时序、清单过滤、并发、故障隔离、取消与审批保留测试。当前父实例已实测自动拉起原 `eva-000`，workspace UUID 和固定主会话 ID 保持不变，没有调用显式启动/唤醒或新建其他节点；这不等于已验证浏览器视觉或模型调用。

这是用户启用的插件启动策略，不是 Agent 工具的免审批通道：助手显式调用 `eva start` / `stop` 仍保持原有单次审批，浏览器新建和唤醒入口的身份校验也不变。重新加载 EVA Host 会先停止它管理的进程，新插件就绪后再恢复已有节点，可能中断进行中的任务；更新前仍应确认影响。

## 插件自带 skill

EVA 父 Host 随插件注册原生 `magi-eva-nodes` 操作 skill，覆盖选取/复用节点、新身份分配、审批、状态含义和失败诊断。详见[操作指引](<skills/eva-nodes/SKILL.md>)。资源从安装包定位，随插件打包；原生 `skills` 服务可选，移除服务或卸载插件时注销，恢复后重新注册，不影响 EVA 核心能力。子 Host 不注册启动器，也不注册这份管理 skill。skill 不授予额外工具或权限；源码变更不等于运行实例已更新。

## 工具

```json
{"action":"start"}
{"action":"start","handle":"melchior"}
{"action":"status"}
{"action":"status","handle":"eva-000"}
{"action":"stop","handle":"eva-000"}
```

- 有 `handle` 就使用它；格式为 `[a-z][a-z0-9-]{0,31}`。保留 `name` 作为兼容别名，但不能与 `handle` 冲突。
- 不给 handle 的 `start` 只读取用户主目录的直属名称，从 `eva-000` 起选首个未占用编号。不递归搜索、不扫描 Documents/桌面/下载，也不由调用工具的 Agent 自行分配路径。使用原子目录创建应对并发，不复用已有文件、目录或符号链接。编号至少三位，可自然增长到 `eva-1000`。
- **每次无 handle 的启动创建一个新节点**。使用返回的 handle 再次启动才会复用当前进程；不同 Session / 调用工作区中的相同 handle 指向同一节点，不把 Session 当节点。
- 正常停止保留数据目录和工作区，所以编号仍被占用。取消、初始化失败或分配过程中退出也可能留下保留目录；不自动清除用户数据来回收编号。
- `status` 无 handle 时列出当前管理的节点及已知的持久化节点（含离线记录）；指定 handle 则查询该节点。只读取主目录直属 `.dsh-<handle>/eva` 的角色标记或旧版固定主会话映射；跳过符号链接、损坏或无关目录，不递归搜索、不读取 profile、凭证或磁盘 PID。发现的节点返回 `not-started`，仅表示当前父 Host 未管理其进程，不判断或接管外部进程。`stop` 必须指定 handle。
- `start` / `stop` 通过 DSH 原生 approval 请求单次批准；拒绝、取消或无审批通道均不执行。自动编号也在批准之后才分配目录。
- 成功启动返回 handle、PID、profile、workspace、独立数据根、实际 cwd、新对话有效默认预设、workspace ID 和不带认证信息的 origin。

## 独立 DM 插件（本次源码重构，尚未重载）

DM 工具、持久投递、去重、原生 Session 通知和 Client 渲染统一归属 [@magi/dm](<../../packages/dm/README.md>)，EVA 通过依赖安装它，并由 [bundle patch](<cordis.patch.yml>)挂载独立根插件。桌面经同一 EVA bundle 复用 DM，不再保留第二份实现。

EVA 只提供可信 `magiDmTransport`：固定 handle、工作目录、进程 IPC 与在线清单；桌面 Shell / 子 Host 启动器继续负责 `magiMainSession.current()`。DM 可独立重载并重新接入仍在运行的子进程；重载 EVA 本身仍会停止子节点。工具 schema/说明、`magi-dm` 数据目录、身份、wire/source ID 和 Session ID 均保持兼容，不迁移或清理历史。

子 Host overlay 版本为 **3**，分别挂载 [EVA 生命周期](<child.js>)与从已安装依赖解析的 DM **绝对根入口**，不引用仓库相对路径。已选择旧/新 EVA bundle 的子 profile 会继续保留其 manifest；仅禁用其中可能存在的 DM 根行，避免同包双 Client 来源。旧版本没有该行时，原生 Loader 只警告并跳过该禁用目标，显式 DM 子入口仍加载。版本与解析入口路径进入不可变 patch hash，旧启动文件保留原字节。

下面的真实投递与页面验收记录属于重构前已应用实现；不能据此声称本次包拆分已在当前运行实例验收。应用本次更新仍需新的用户确认和安全窗口。

## 同 Bot 跨会话通知：`@user`（重构前真实投递及页面验收通过）

在任意非主 Agent Session 中调用：

```json
{ "action": "send", "to": "@user", "text": "test 12345" }
```

- `@user` / `user` 是保留地址，表示**当前 Bot 自己的用户 / 固定主会话**。EVA 不会将其转给桌面主会话，也不经过父 IPC；父连接暂时不可用仍可本地发送。普通 Bot 地址也接受一个可选 `@` 前缀。`contacts` 包含此用户地址；Bot 固定 handle 不得为 `user`，已有冲突需要人工处理，不偷偷改名。
- [通知域](<../../packages/dm/dm-user.js>)从启动所有者提供的可选 `magiMainSession.current()` 获取已经就绪、固定的 Session ID，并冷检查身份 / workspace / 非分支非 seed。缺失、未就绪、被替换或卸载均失败关闭，绝不创建替代主会话。当前已经在主会话时直接回复用户，工具拒绝自发 `@user`；调用源只取原生执行上下文，不接受模型自报 Session。
- [DM Client 投影](<../../packages/dm/client.js>)通过原生 Conversation Definition 与独立 Chat 节点显示 Agent 侧正文，**不加 `[MAGI-DM]` 或发送方格式头**，不伪造原生模型 `assistant/message`。正文作为纯文本安全渲染。通知直接由持久化原生 inbox splice 显示，不等待模型生成、不会额外唤醒主 Agent。UI 插件 / 页面必须加载更新后才有此显示。
- 原生 `inject` 是不唤醒的 next-step 输入，不是原生助手消息 API。其后续模型上下文包含单独的内部来源说明与 JSON 引用正文，明确“已经展示 / 非人类指令与授权 / 不要重复回复”，**这些内部说明不显示在通知正文或 Bot 预览中**。若主 Agent 正在执行，通知可在其下一原生步骤被纳入背景上下文；不承诺对运行中的上下文零影响，不绕过权限 / 审批。
- 本地 outbox 保留显式通知和固定主会话目标，原生持久化 flush 后才返回 `accepted`；稳定 ID 重试及崩溃恢复复用已有原生通知，不重复显示。通知的 `message_id` 还绑定调用 Session，其他 Session 不得抢用；`history` 可查 `@user`，不支持对此地址使用 `reply_to`。`accepted` 只表示通知持久接收，不表示人已读或 Agent 已执行。
- 这是既有 Bot 间 DM 的本地分支；下面的 Bot 间身份绑定、独立 pair Session、显式正文、可靠投递和不自动礼貌回声规则不变。用户确认更新窗口后，已通过 Plugin Manager 重新应用父入口并正常恢复 `eva-000`；原 workspace UUID 与固定主会话 `session-955a2fee-7c8e-4a5e-9cc9-406ca72e2a7e` 保持不变。
- 已真实执行 `desktop → eva-000 → @user`：桌面发送 `desktop-user-test-20261027-2025`，子 Bot 自己的工具发出 `eva-user-test-20261027-2025`；子 outbox 核验 `from:eva-000` / `to:user` / 精确正文 `test 12345` / 原固定主会话 / `accepted` / `attempts:1`，来源是其独立 DM Session。原生 Session Query 驱动的实际主会话预览也返回精确 `test 12345`，子端再重启后仍保留且没有重投。用户已在实际页面明确确认：**Agent 左侧、正文仅 `test 12345`、无 `[MAGI-DM]` 前缀、只有一条**。视觉验收来自用户确认，不是 Agent 自行截图或 DOM 检查。
- 历史 v2 曾用 EVA 单一合并入口避免同包双 Loader 来源；本次已移除该包装器，改用两个独立包根与 v3 overlay。[原生元数据探针](<test/client-modules-native.js>)使用官方 Context / Loader / ClientModuleRegistry，验证独立 Client 发现、已选 DM 行禁用、旧行不存在时仅警告，以及两行同时启用时确实拒绝重复来源；不执行 Host 生命周期，不等同于运行页面验收。
- 25 个源码模块 / manifest 检查通过；最终完整回归 **518 项中 517 通过**，唯一失败为既有依赖策略检查缺少本地 `semver`；未安装或跳过该依赖。新增通知 Host 41 项及 Client/预览联合 47 项通过，包含真实原生 Session 验证与 restore 后的 Conversation assembler 正文检查；单入口相关定向回归 90/90，通过原生预设探针的 30 个模块导入与 Creator 初始声明对比。

## 本地 Bot DM（已应用，真实链路通过）

新增 Host 工具 `dm`，不新增聊天 UI、群聊、HTTP 端口或远程 Host 控制。父 Bot 与 EVA、同一父进程下的 EVA 之间复用既有进程 IPC；发送身份由父进程持有的子进程及 handle 绑定，不信任消息自报身份，也不使用浏览器认证或共享凭证作为 DM 授权。原生 `send_message` 仍只服务子 Agent 谱系，不用于 EVA。

```json
{"action":"contacts"}
{"action":"identity","name":"桌面助理"}
{"action":"send","to":"eva-000","text":"请检查这个方案的风险，并给出结论。","message_id":"review-001"}
{"action":"status","message_id":"review-001"}
{"action":"history","to":"eva-000","limit":20}
```

收件正文只标记 MAGI-DM 来源和发送方 handle，不展示消息 ID 或回复关联；这些仍保留在内部信封、持久化记录及工具结果中：

```text
[MAGI-DM] from @desktop:

请检查这个方案的风险，并给出结论。
```

- 每个 profile 首次初始化固定 handle（例如 `desktop` / `eva-000`），另存可修改的显示名。路由只使用 handle；改名不改地址，也不覆盖 MAGI 导航名称。
- 每对 Bot 只有一个逻辑 DM ID；双方各自保存自己的普通原生 Session、原生历史及显式收发记录，不共享 Session 文件，也不固定或覆盖用户预设。DM Session 与固定主会话分开，不新增专用导航入口。
- 仅 `send` 的 `text` 跨 Host；不会自动转发助手末尾、工具输出或 Session 完成事件。显式发件文本也会以带来源的原生 `inject` 输入记录在发送方 DM Session，**不唤醒发送方模型**，使后续回复有原问题上下文；首次入站消息用原生 `followup` 驱动接收方，不另建 Agent 执行循环。收件方沿用自己的工具权限和审批，Bot 消息不是人类授权。
- 防止礼貌回声的模型规则只放在 `dm` 工具说明，不修改人格、技能或每条消息提示。接收不要求回复，只有实质内容才显式发送。
- `pending` 表示本地持久排队；当前 Host 运行时每 5 秒重试，重启后继续处理 outbox，不自动启动离线 Bot。同一发送方的 `message_id` 只能重试同一收件人、正文和 `reply_to`，重复投递不会重复入队；不同 peer 可各自使用相同 ID，收件记录与恢复按发送方隔离。`reply_to` 必须引用该 peer 的本地入站消息。正文非空、最多 16 KiB UTF-8，序列化信封最多 40 KiB；每端待接收/待发送上限各 128。
- **`accepted` 仅表示接收方的原生 inbox 已持久接纳，不表示模型已执行、任务完成或对方回复。** 回执和重试由程序处理，不产生额外模型轮次。
- **重启恢复采取 fail-closed 审计，不保证自动继续执行。** 已接受消息只检查原生日志，不激活 Agent、不取消重插、不制造控制输入，不撤销用户停止。原生队列仍 pending 时，下一条真正 DM/原生用户输入才自然唤醒；已 claimed 但未有用户消息提交时可能是取消、失败或崩溃，不盲目重放。历史中的 `attention` 为恢复检查标记（pending / consumed-before-commit / evidence-missing），不是实时执行状态。
- 私有 DM 状态位于各自 profile 的 `magi-dm` 目录，目录 0700、文件 0600；包括固定身份、pair 映射、inbox/outbox，采用原子写入及 fsync。符号链接、硬链接、损坏身份或映射均拒绝。固定 Session 缺失、被替换、删除或创建中断时失败关闭，需要人工核查，不另造替代会话。状态当前不自动清理，是显式消息记录而非第二套模型历史。

初次实现时仅做离线验证；现已在用户明确批准更新窗口后应用父插件并正常恢复子节点，完成真实 `desktop → eva-000 → @user` 与页面验收，详见上节。旧运行节点没有 DM 握手时在 contacts 中显示 offline，不会被工具偷偷重启。以后重新应用父 EVA 仍可能停止其管理的子节点，须先确认影响；仅刷新页面不会应用 Host 改动。

最终使用 MAGI 自带 Node 执行 EVA + MAGI Shell 联合回归 **419/419 通过**，其中 DM 领域 23、IPC 20、私有存储 15、插件路由/工具/生命周期集成 10、子入口依赖解析 2 项；另覆盖真实 fake-CLI 进程 attachment 生命周期、启动 overlay 版本保留和父入口桥接。模型/Session 部分使用窄范围原生接口夹具，未调用付费模型，不等同于真实跨 Host 模型或浏览器验收。

实现集中在独立包：[本地领域与原生会话](<../../packages/dm/dm-host.js>)、[私有持久化](<../../packages/dm/dm-store.js>)、[有界 IPC](<../../packages/dm/dm-rpc.js>)、[Host 生命周期与路由](<../../packages/dm/dm-plugin.js>)、[工具定义](<../../packages/dm/dm-tool.js>) 和 [插件根入口](<../../packages/dm/index.js>)。

## 防止 EVA 嵌套

启动子 Host 前，在其数据根的 `eva/node.json` 持久化 `{"role":"eva-child"}`。EVA 插件加载时先检查当前 `profileContext.home` 下的标记；属于子节点便不创建管理器、不注册 `eva` 工具或父节点管理路由。即使在子节点安装或重新加载 EVA，也不会提供下一层启动器。

子节点的嵌入界面、固定主会话及其他插件能力保持不变。标记随数据根保留；旧节点下次由新版 EVA 启动时创建。标记损坏时拒绝启用管理器，不自动当作普通 Host。这是防嵌套约定，不是 OS 安全隔离。

## MAGI 工作台与固定主会话

节点清单与选择入口位于独立的 MAGI 工作台；原生 DSH 的「插件」「提醒」附近不再注册 EVA-000、EVA-001 等重复入口。在线和离线节点仍由清单 API 返回，节点停止后保留记录；父 Host 重启后从持久化标记重新发现。运行节点仍通过官方 Session Controller 创建或复用一个固定主会话，持久化其 ID。

Bot 条目的第二行显示最近用户/助手消息的单行预览，在线状态仅用圆点表示，不再重复「在线/离线 · 主会话」。子 Host 通过原生 Session Query 读取自己的固定主会话，将长度受限的预览经已有私有进程 IPC 交给父管理器；不读取其他 profile 的 Session 文件，不传递登录地址、工具输出或思考内容。父清单只公开允许的预览字段。空会话与暂时无法读取的预览使用不同占位，不把读取失败当作没有消息。

为兼容已经打开的父面板，保留原有 `main` 面板地址及其状态、重试和唤醒处理，但不再提供原生侧栏导航。以下唤醒按钮说明仅适用于这些兼容面板；MAGI 工作台当前提示用户切回 DSH，通过 EVA 工具唤醒原 handle。兼容面板的离线视图不请求子页面、不自动启动，明确点击「唤醒 EVA」才复用原节点数据与固定主会话，不自动分配新节点。

唤醒中按钮禁用，重复点击按 handle 合并；切换面板或状态轮询重挂载不会取消启动，插件卸载仍统一清理进程。失败显示本地化原因并允许重试；启动中和停止中的节点不提供新的唤醒动作。启动成功后，常规轮询会更新状态并打开主会话。

MAGI 的「新 Bot」使用显式 POST `/api/eva/create`，与 `eva start` 共用管理器的无 handle 自动分配与启动逻辑，创建独立数据根和固定主会话，而不是在父 Host 新建普通会话。并发创建请求在启动期间合并，UI 禁用重复点击；不做自动 POST 重试。切换界面不会通过请求取消来放弃进程归属，仍由管理器统一负责启动、失败清理和卸载停止。此入口与下面的唤醒入口使用相同的原生浏览器身份校验；Agent 仍必须调用原 EVA 工具走审批，不向子 Host 注册创建路由。

唤醒使用已认证 Connection 的 POST `/api/eva/wake?handle=…`，只允许当前已登录浏览器的原生 `browserPeer`；同属 owner 的 API Peer 也不能用此入口绕过工具审批。助手调用 `eva start` / `stop` 仍走原生单次审批。当前运行时的 Fetch 路由不传递 Peer，因此通过原生 `connection.admit()` 重新验证请求凭证；无法核实浏览器身份的桌面 IPC 连接会安全拒绝，请使用已登录的父实例网页，或让助手通过原工具审批启动。不修改 DSH 底层能力来绕过这个限制。

MAGI 工作台（以及保留的兼容面板）只在父实例的**右侧主区域嵌入子 Host 的原生对话页面**，父实例地址、原生导航和其他会话保持不变。通过已认证的 transport POST 请求取得子节点专用嵌入登录地址，再加载独立 iframe。嵌入入口由原生 Connection 校验登录令牌，保留签名、有效期与 HttpOnly，仅将 Cookie 设为 `SameSite=None; Secure; Partitioned`，解决桌面 `file://` 父页面的跨站 Cookie 限制（Chromium 将 loopback 视为可信来源）；独立打开仍使用原生 `SameSite=Strict` Cookie；不再让父页面访问需要认证的跳转路由。子页面通过官方 `shell.overlay` Slot 添加自有条目，不再替换或卸载原生侧栏及其后代功能席位。仅在嵌入模式挂载隐藏标记和声明式 CSS：以原生右列标记定位实际三栏容器，而不假设 Slot 包装层级；左栏网格项退出布局，主内容跨越左、中两列，同时隐藏残留的左侧拖拽柄和重新展开控件。不使用偏移裁剪、不修改官方插件文件，保留原生右侧栏、浮层及底部区域。固定主会话导航在浮层实际挂载、Session 与工作区目录均就绪后才调用原生 `openSession`；随后核实该 ID 的 `mainView` 引用和 binding，并等待会话历史 `openState: open`，才移除加载遮罩和一次性 URL 标记。原生导航抛错、引用未建立或历史打开失败时显示错误及显式重试按钮，不把新会话页面当作成功、不创建替代会话。样式仅在子页确认为嵌入模式时挂载，离开或卸载即移除；独立打开子页面时仍显示完整原生界面。此适配面向当前 0.2.1-rc.3 的三栏布局，官方容器结构升级后需复核。iframe 不允许顶层导航。认证链接不进入节点状态 JSON、工具结果或模型上下文；助手通过 `eva` 工具发起的启动和停止仍请求原生单次审批，用户按钮唤醒独立校验浏览器身份。

### 切换 EVA 时保留已加载页面

此前每次切换 `main` 面板都会卸载 iframe，重新 POST open、嵌入登录、启动整套子页面并打开历史。现在父 `main` 面板只提供插件自己的定位占位，实际 EVA 页面由根级 `shell.overlay` 中的常驻容器持有；只测量自有占位的矩形并对齐中心区域，不替换原生 root/Conversation、不修改官方文件、不在插件外追加或移动 DOM。

首次选择才加载；当前父页面内，所有已经打开的 EVA 页面持续保留，**没有页面数量上限，也没有 LRU 淘汰**。切到主机器人、其他 EVA 或原生面板时只隐藏、禁用交互和辅助技术访问，切回不重新登录、不替换 iframe，也保留该页面的原生输入和滚动状态。不会预先获取所有节点的认证链接。原来的三页限制没有实测依据，已删除；不以猜测的内存数字另设限制。

普通清单/消息预览更新不重新注册主面板。节点停止、消失、工作区或主会话身份变化、实例代次变化会释放旧页面；显式重试仅重建该节点。清单新增可选的非秘密 `instanceId`（由运行中 PID 与 origin 摘要生成），用于识别两次轮询之间已经重启但主会话 ID 不变的进程；不公开 PID、origin 或认证 URL。插件卸载统一清理页面、未完成请求、监听和定时器。保活仅在当前父页面生命周期内有效，整页刷新或插件重载后仍有一次首次加载。

清单请求短暂失败时保留已有页面；iframe 网络错误或可见等待超时只覆盖错误/重试提示，不销毁 iframe。原窗口恢复并发出有效就绪消息即可继续显示，不再次 POST open；只有用户明确重试才准备新认证导航并重建页面。子页自己的固定主会话打开失败仍显示原生重试界面，不伪装成成功。

本次 EVA + MAGI Shell 联合回归 **343/343 通过**，覆盖八页保留与往返、清单短暂失败、超时/iframe 错误后的同页恢复、显式重试、实例代次与清理，以及下述观测逻辑。这些是窄范围 VM 声明/生命周期测试，不是实际浏览器 iframe DOM、视觉布局或切换耗时实测。当前 GUI 已通过新 `EVAFrameTiming.snapshot` 的真实响应确认加载新 Client，`pageLimit:null`；没有为此重载 Host，`eva-000` 的 PID、workspace UUID、固定主会话和预览保持不变。不据此承诺其他改动自动热更新。

打开过程中，父面板始终显示同一位置、同一主题背景的加载提示；iframe 保持实际布局但透明且不可交互，避免先露出子页面默认界面、侧栏收起或会话切换的中间画面。子页在固定会话就绪后的渲染提交再经过两次动画帧回调，才发送就绪消息；父面板核对 iframe 窗口、精确 origin 与主会话 ID 后一次性显示。该通道只传递会话 ID 和状态，不传认证地址；失败时露出子页错误及重试，未收到消息的可见等待超过 30 秒则提供父面板重试，后台标签页暂停计时。子页重载或重试重新遮罩，卸载清理监听、计时器和动画帧；不使用固定延时强行显示。

#### 加载观测与内存边界

父页提供可选只读 Client Inspect Provider `EVAFrameTiming`，方法 `snapshot({})`。它只复制现有计数和计时，不导航、不唤醒、不探测子页、不触发内存采样。观测由父浏览器 `performance.now()` 记录：首次选择到经验证的子页 `ready`（`initialChildReadyMs`）、首个认证请求准备耗时（`initialPreparationMs`），以及自己的定位占位挂载到下一个动画帧回调（`anchorToAnimationFrameMs`）。最后一项只是调度时间，不是用户点击到屏幕显示的延迟，也不是子页重新加载耗时；`readyOnLastVisit` 区分返回已就绪页面。首次打开经过隐藏/切走会标记 `backgroundDuringInitialOpen`，耗时包含后台墙钟时间。

`failed`、网络错误、超时与 `ready` 分开记录；失败后恢复保留 `firstOutcome`，只增加 `recoveredReadyMs`，不把失败改写成成功的首次打开。重复就绪消息不重写原计时。每个保留页面仅固定数量标量，无逐次访问历史；输出至多最近创建的 50 个页面摘要，此限制只针对诊断输出，不淘汰页面。返回数据不包含认证 URL、origin、Session ID、工作区、消息内容或原始错误；插件卸载清理 provider 和记录。

当前 GUI 实际返回 `retainedCount:0`（新代码加载后尚无打开样本），所以**没有可报告的首次打开/切回毫秒数**；不能用测试夹具的模拟时钟当实测。实际浏览器能力为 `secureContext:true`、`crossOriginIsolated:false`、`applicationMemoryApiAvailable:false`，未采集内存数值。跨 origin 的独立 iframe 不能从父页 JS heap 推算每个 EVA 的准确内存；`memory.status:not-measured`、`perFrameBytes:null` 明确未知，不输出 0 MB，也不把 Host RSS 当浏览器内存。不修改隔离头来强行开启测量，不预先重写为跨 Host 单一聊天 UI。

固定主会话映射保存在节点的 `eva/main-session.json`。首次创建前先保留身份，再创建、检查并刷新持久化；重启只检查和复用同一 ID，不因点击面板重复创建。映射损坏、会话缺失、身份不匹配，或创建前保留记录遗留但找不到对应会话时，均拒绝启动，不自动替换或删除记录；需人工检查映射及 Session 存储。

启动还会通过官方 Workspace 实体的 `attachSession` 将固定主会话加入其不可变头部 cwd 对应的工作区；没有该注册时，仅注册已有目录，不创建目录或修改会话工作目录。归属关系持久化并可见之后，才开放主会话视图和发布启动成功。旧版升级会话仍归属于原始头部目录，不强行挂到节点根目录。此步骤也会修复之前已创建但未加入工作区的固定会话：原生空白对话不再因缺少归属而要求重新选择工作区并创建其他会话；已经产生的其他会话及历史保持原样，不自动合并或采用它们作为主会话。

固定主会话标题使用节点名的大写形式（例如 `EVA-001` 或 `MELCHIOR`），与侧栏名称一致。每次启动通过官方 `sessionQuery.readTitle` 检查，只在名称不同或尚未由用户来源固定时调用 `sessionController.rename`，刷新持久化完成后才开放主会话。原生用户标题会终止尚未完成的自动生成，并阻止后续聊天内容自动改名；已经固定的同名标题不重复追加事件。旧版聊天标题会在下次启动时更新，仅追加标题事件，保留主会话 ID、历史及其他会话名称；不修改官方插件或只覆盖界面文字。

## 同构目录与隔离

相对于主机用户目录，默认布局如下，不再嵌套在调用工作区内：

```text
~/.dsh/                                      # 主实例数据根；凭据库经官方 provider 与本机 EVA 共用
  profiles/desktop/
~/.dsh-eva-000/                              # 子实例 DSH_HOME、进程 cwd 和主工作区
  profiles/eva-000/
  profiles/.eva-assets/eva-000/
    preset-eva-<content-hash>.yml               # 官方插件快照；保留原生 profiles 解析范围
  storages/                                 # 工作区注册等状态
  eva/
    startup-<installation-id>-<startup-config-hash>.patch.yml
    owner.lock
    main-session.json                         # 固定主会话身份与持久化状态
~/Documents/deepseek-harness/
  default-workspace/                         # 主工作区
```

若显式传入 `melchior`，则使用 `~/.dsh-melchior` 作为主工作区、`DSH_HOME` 和进程 cwd，profile 位于其下 `profiles/melchior`。新节点不创建额外的 `workspace/` 子目录。每个节点只需持久化一个根目录；后续 Docker 启动器可把它作为一个挂载点，配置容器内的 `DSH_HOME`。注册工作区后，通过 Session Controller 创建或复用该工作区的固定主会话；仍可使用原生工作区功能创建其他会话。跨环境更改绝对路径时还需调整注册路径；本版尚未实现 Docker 启动。外部技能或其他宿主机资源不自动包含在此挂载中。

子进程复用应用附带的 Electron Node-mode 和 DSH runtime，官方 CLI 仅在首次启动时从 `web` 模板创建 profile。随后保留已有 profile；拒绝将非空、没有 manifest 的目录当新 profile 初始化。只绑定 `127.0.0.1`，动态端口，传入 `--no-open`；不启动额外 Electron 桌面，也不弹浏览器。这是持续运行的 Web Host，不是执行一个 prompt 就退出的 headless profile。

只有 `appReady`、工作区注册、固定主会话创建或复用及持久化刷新完成，并经 IPC 核对 PID、profile、profile 目录、workspace、DSH_HOME 以及 cwd 等于 DSH_HOME 后，才报告运行成功。正常 HTTP 入口仍需认证，origin 不是登录链接。不提供子节点发任务、模型配置或打开 UI 的模型工具；打开主会话由 EVA 面板中的用户导航完成。

停止时先 SIGTERM，超时后强制停止；macOS/Linux 使用专属进程组清理。插件卸载和父 Host 退出会停止节点。正常退出删除拥有的运行锁；异常断电的遗留锁要求人工检查，绝不按磁盘 PID 盲目杀进程。

## 本机共享登录与凭据

从本机启动或唤醒的节点默认**直接共用父 Host 当前官方本地凭据库**，无需复制令牌。登录、退出、切换账号、授权失效后的删除以及已保存 API key 的修改，会经官方文件监视传播到所有共享实例；传播有短暂延迟，已在途请求不保证撤销。共享范围是整个凭据库，包含插件授权记录和设备身份 UUID，不仅是 DeepSeek 登录。节点 Session、profile、状态、工作区与进程 cwd 仍独立。

EVA 从当前官方 `LocalCredentialProvider` 的公开 config 与导出 resolver 获取有效绝对路径，尊重自定义 `path` / `dshHome`，不假定主实例数据根就是凭据位置。只传递路径，通过启动 overlay 将子节点的官方 `credentials` 配为相同路径且 `watch:true`；不读取、复制、合并或预创建凭据文件。未登录、文件尚不存在可以正常启动。父 provider 为自定义实现或关闭监视时拒绝启动；子节点须核验实际 provider、路径和监视配置才发布就绪，不能覆盖的自定义子 profile 会失败，不谎报共享。

已有节点自己的凭据文件保持原字节，但被共享配置遮蔽，不自动合并、删除或启用；旧启动 overlay 不覆盖，路径和共享协议变更生成新的不可变 overlay。API key 环境变量仍按原规则从子进程环境剥离，父/子 `.env` 不自动同步。凭据值不进入环境、IPC、工具结果或模型上下文；IPC 仅返回共享核验布尔值。

DeepSeek 父子账号 provider 必须使用同一 `platformOrigin` / 授权签发方。自定义或私有平台配置不会随凭据库自动复制，EVA 不静默转移请求头或其他秘密配置。远程节点的授权策略留待后续设计，本版仅实现本机共享。

**启动和本地凭据传播验证不等于实际账号登录或模型调用验证。** 全局技能（如 `DSH_AGENTS_HOME`）、同一 OS 用户和应用 runtime 仍可能共享；这不是容器、OS 沙箱或安全边界。主实例凭据现在可由子实例的官方 provider 写入。子进程诊断不进入工具结果，避免认证 URL 或第三方密钥进入模型上下文。

## 内置 Codex provider

凭据共享不等于 provider 配置共享：`llm-pi-ai` 的空 `providers` 字典不会注册任何模型路由。0.6.15 起，新节点的启动 overlay 显式启用官方 `openai-codex: {}` 路由，与默认 DeepSeek provider 并存；由安装的原生 catalog 决定协议、端点与模型列表。Codex 直接使用共享库中的官方授权记录，不复制授权或父实例的 headers、baseURL、模型覆盖与其他 provider 配置，也不改默认模型选择。

子 Host 须确认 `llm.listProviders()` 包含 `openai-codex` 且原生模型 catalog 非空，才发布 `codexAvailable:true` 就绪标志；父 Host 缺少核验确认会拒绝就绪并清理进程。该检查不发起模型调用，也不证明 OAuth 授权当前有效。共享库中没有 Codex 授权时，provider 仍可见，实际使用前需要登录。启动 overlay 是部署默认；子实例运行中的设置仍由原生设置服务管理，不双向同步。

## 专用子端装配与 eva 预设（已随本轮更新应用）

桌面端继续加载 MAGI Shell，统一导航桌面主 Bot 与 EVA。子端由[专用 Host 装配](<child-host.js>)负责生命周期、DM、Agent 默认预设及原生模型/凭据路由；首次 profile 仍通过官方 CLI 的 `web` 模板初始化，复用原生 Session、权限、存储和页面，不复制桌面 profile 或其插件列表。

子端默认不加载 `@local/magi-shell` 或父节点 `@local/eva` 管理器。已有子 profile 若选择了这两个已知 bundle，启动 overlay 只禁用相应管理入口，不卸载包、不改写 manifest 或用户 patch；其他自定义插件保持用户所有。此约束不充当任意插件过滤器或 OS 沙箱，角色标记仍作为防嵌套后备。

通用 [Bot 声明](<../../packages/bot-mode/presets/bot-preset.yml>)归 bot-mode 所有，以官方 Creator（`cordis`）插件配置为初始快照，而不是动态继承：保留同样的工具、persona、技能、规划模式、压缩和委托隔离。技能目录通过 profile 的安装锚点定位官方资源，不相对私有 YAML 副本解析。EVA 通过[包内读取器](<../../packages/bot-mode/presets.js>)生成同插件正文的 `bot-mode` 与兼容 `eva` 两个原生声明，不再维护第二份能力清单，不承诺缓存目录能解析任意 profile 私有包。

启动 overlay 只将子实例 `agent-preset-registry` 的部署默认候选设为 `bot-mode`，不修改父实例默认值。首次固定主会话及之后新建会话使用有效默认；用户已保存的默认选择、会话显式选择仍优先。保留旧 `eva` ID 供历史会话恢复，不迁移 Session 或强行重组；重启时原生 DSH 按记录的 preset ID 恢复当前声明，不代表历史 revision 永久固定。

Bot 预设保留 Creator 的 `plugin_manager` 能力，**不是自动提权**：原生权限与单次审批规则不变；拒绝或没有审批通道时不会执行。启动探针仍从 `agentPresets.resolve()` 核验有效默认，结果中的 `defaultAgentPreset` 是实际值，不强行改回部署候选。

每次启动将声明保存为私有、内容寻址的不可变 YAML；声明内容 hash、Host 装配版本及已知桌面入口禁用列表共同进入新的启动 patch hash。旧 patch、旧声明和已有 profile 保留原字节，篡改同名运行文件时拒绝覆盖。已经运行的节点直接复用，不因查询或重复 start 在线切换装配。

历史 `eva` 实现曾在用户批准的窗口随 `@user` 更新应用并正常恢复原节点；当时确认默认为 `eva`，原主会话、workspace UUID 与历史保持，真实 DM 模型转发通过，预设菜单及手动切换未单独验收。这不代表本次 bot-mode 源码已部署；本次没有改运行 profile 或重启节点，刷新页面也不会应用 Host 更新。

## 旧目录迁移

### 0.5.1：主工作区直接使用节点根目录

启动时通过官方工作区注册服务追加注册 `~/.dsh-<handle>`。已有的 `workspace/` 子目录、旧版 `~/Documents/deepseek-harness/<handle>-workspace` 及其他注册工作区保持原位置、文件与历史；不自动复制、搬移、删除或改写旧记录。新节点不创建嵌套工作区。历史版本的自动收拢逻辑不再用于启动路径；遗留的 `eva/workspace-relocation.pending.json`、离线迁移标记、未完成的工作区 mutation 或不安全的存储元数据仍会阻止启动，需人工检查，不要删除标记盲目重试。

旧版固定主会话通过官方 `workingDirectory.set` 将**有效工作目录**改为节点根，保留原 main Session ID 和全部历史。不可变的会话头部 cwd 及历史来源/工作区分组不重写，因此旧会话仍可能显示在原工作区分组下；这不表示历史记录已搬到根工作区。新建固定主会话直接在节点根创建，原生分组也对应根目录。升级失败保留旧数据并拒绝启动，不另建替代主会话。

### 0.1：离线转换工具

0.1 版的 `.eva/nodes/second/{home,workspace}` 可在**先停止该节点后**用离线迁移脚本迁移：

```sh
'/Applications/MAGI.app/Contents/Resources/runtime/bin/node' migrate-layout.js /absolute/workspace/.eva/nodes/second
```

脚本扫描并分配新的 `eva-NNN`，保留子实例自己的文件字节，改名复制后的 profile，并更新 workspace 路径、保留 UUID。不读取或打印凭证内容；不触碰主实例数据。原目录完整保留为回退副本，留下迁移锁，防止旧版本继续启动这个副本。

迁移不覆盖目标、不跟随源符号链接；已有运行锁、历史会话或 pending workspace mutation 均拒绝处理。未完成的副本保留 `eva/migration.pending.json`，运行时拒绝启动它。遇到失败请检查锁和 pending 标记，不要直接删除后盲目重试。

## 开发验证

在本目录运行（使用 MAGI 自带运行时，不使用系统 Node；详见 [仓库要求](<../../AGENTS.md>)）：

```sh
'/Applications/MAGI.app/Contents/Resources/runtime/bin/node' --test test/*.test.js
```

新增[预设原生只读探针](<test/preset-native.js>)可验证原生 YAML 解码、初始 Creator 配置一致性、全部 30 个原生模块及技能路径；不启动 Host、不创建 profile 或写盘。该探针对比初始快照，不是长期继承约束，未来有意定制 EVA 时应同步调整比较基线：

```sh
ELECTRON_RUN_AS_NODE=1 '/Applications/MAGI.app/Contents/MacOS/MAGI' --expose-internals \
  apps/eva/test/preset-native.js \
  '/Applications/DeepSeek Harness.app/Contents/Resources/app.asar/dsh/package.json'
```

以上命令从仓库根目录运行。实际 Agent 激活、模型调用和 GUI 菜单仍需安全更新后另验。

本轮定向回归 **130/130** 通过，完整当前产品入口 **456 项中 455 通过**；唯一失败为既有[依赖策略检查](<../shell/test/dependency-policy.test.mjs#L12>)缺少本地 `semver`，并非装配回归，不代表完整入口全绿。资源 / manifest / 23 个源码模块检查和上述原生探针均通过。未安装依赖、跳过检查或重载已有 Host。

0.6.15 最初发布时的本地自动化测试共 236 项，覆盖凭据路径解析、拒绝非官方 provider / 禁用监视、启动 overlay 与旧文件保留、子 provider 核验及失败锁清理，增加原生 Codex 路由、模型 catalog 与就绪失败回归，并保留目录隔离、固定会话、标题、工作区与嵌入就绪回归。工具说明新增共享副作用提示；说明字符数 1048 → 1114，按字符数 / 4 粗估常驻提示增加 17 tokens（非模型 tokenizer 实测）。

[真实 runtime 集成测试](<test/integration.js>) 使用隔离的临时 `HOME` 与应用的 Electron Node-mode，参数为安装锚点与测试调用目录；覆盖审批拒绝不分配目录、自动编号、旧目录与历史保留、重复启动、HTTP 401、认证视图、停止、重启和卸载清理。Host context / approval / 父 provider 发现边界是测试夹具；子 Host 与有效 provider 是实际服务。

[共享凭据集成测试](<test/integration-credentials.js>) 自行建立临时 homeRoot 和不存在的合成凭据路径，启动两个实际 DSH 子进程；测试插件仅用固定的合成记录，通过 IPC 布尔断言核验双向更新、删除传播与 20 次并发 read-modify-write 无丢失，并确认旧子凭据文件字节不变。等待官方变更事件后再检查缓存，不以写入 ACK 当作其他进程已刷新。测试结束停止子进程并清理已核实的临时目录，不读取真实凭据、不进行实际账号登录、退出或模型请求。

在仓库根目录运行共享集成测试（ASAR runtime 需要 Electron Node-mode）：

```sh
ELECTRON_RUN_AS_NODE=1 '/Applications/MAGI.app/Contents/MacOS/MAGI' --expose-internals \
  apps/eva/test/integration-credentials.js \
  '/Applications/DeepSeek Harness.app/Contents/Resources/app.asar/dsh/package.json'
```

真实 runtime 测试**不等于当前桌面已安装或加载新版本**；安装状态需另行检查，面板可见性、跳转与实际会话选择需在当前页面验证。实际工具状态以会话中的工具定义及 `eva` 调用为准，不把旧路径、PID 或端口当成当前状态。

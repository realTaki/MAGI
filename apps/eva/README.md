# EVA

DSH Host + Web Client 插件，提供模型工具 `eva`、节点清单与会话嵌入支持，管理额外的无桌面窗口 DSH 进程。节点入口统一放在 MAGI 工作台，不再向原生侧栏插入 EVA-NNN 条目。目标环境为 DeepSeek Harness Desktop 0.2.1-rc.3 / macOS。当前插件版本 0.6.15。

## MAGI 重构起点

本目录已用 DSH Host 插件替换 MAGI 旧的 EVA launcher，作为逐步改造成 DSH 版本的起点。其他旧模块和根级构建流程尚未改造，不能据此认为整个 MAGI 已兼容 DSH。背景、验证边界和后续工作见 [交接文档](<../../DSH-HANDOFF.md>)。

## 安装与更新

1. 左侧「插件」→「添加插件」。
2. 输入本插件目录的绝对路径：`/Users/shichuan/Documents/deepseek-harness/default-workspace/MAGI/apps/eva`。
3. 点击「安装」，完成后「立即启用」。

也可在新的 Creator 会话用 `plugin_manager install_bundle` 安装本目录。入口为「插件」→「添加插件」右侧箭头→「让 Agent 创建插件」；不能将已开始的会话切换成 Creator。

不要手动修改主实例 profile，不要在 profile 内运行包管理器。更新已链接安装的 Host 源码后，可先在 Plugin Manager 关闭再启用 EVA：支持 HMR 的 profile 会比较源码、清除相应模块缓存并重新加载；仅在返回 `restart-required` 时重启主实例。仍需通过实际工具调用确认新代码已经加载，不能把磁盘更新或显示的新版本号等同于当前进程已更新。本插件无需构建或系统 Node/pnpm/dsh 命令。

仅更新 Web Client（例如移除原生侧栏入口）时，刷新父实例网页以重新加载前端；不要为此关闭/重新启用 EVA Host，卸载 Host 会停止其管理的子节点。

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

## 防止 EVA 嵌套

启动子 Host 前，在其数据根的 `eva/node.json` 持久化 `{"role":"eva-child"}`。EVA 插件加载时先检查当前 `profileContext.home` 下的标记；属于子节点便不创建管理器、不注册 `eva` 工具或父节点管理路由。即使在子节点安装或重新加载 EVA，也不会提供下一层启动器。

子节点的嵌入界面、固定主会话及其他插件能力保持不变。标记随数据根保留；旧节点下次由新版 EVA 启动时创建。标记损坏时拒绝启用管理器，不自动当作普通 Host。这是防嵌套约定，不是 OS 安全隔离。

## MAGI 工作台与固定主会话

节点清单与选择入口位于独立的 MAGI 工作台；原生 DSH 的「插件」「提醒」附近不再注册 EVA-000、EVA-001 等重复入口。在线和离线节点仍由清单 API 返回，节点停止后保留记录；父 Host 重启后从持久化标记重新发现。运行节点仍通过官方 Session Controller 创建或复用一个固定主会话，持久化其 ID。

为兼容已经打开的父面板，保留原有 `main` 面板地址及其状态、重试和唤醒处理，但不再提供原生侧栏导航。以下唤醒按钮说明仅适用于这些兼容面板；MAGI 工作台当前提示用户切回 DSH，通过 EVA 工具唤醒原 handle。兼容面板的离线视图不请求子页面、不自动启动，明确点击「唤醒 EVA」才复用原节点数据与固定主会话，不自动分配新节点。

唤醒中按钮禁用，重复点击按 handle 合并；切换面板或状态轮询重挂载不会取消启动，插件卸载仍统一清理进程。失败显示本地化原因并允许重试；启动中和停止中的节点不提供新的唤醒动作。启动成功后，常规轮询会更新状态并打开主会话。

唤醒使用已认证 Connection 的 POST `/api/eva/wake?handle=…`，只允许当前已登录浏览器的原生 `browserPeer`；同属 owner 的 API Peer 也不能用此入口绕过工具审批。助手调用 `eva start` / `stop` 仍走原生单次审批。当前运行时的 Fetch 路由不传递 Peer，因此通过原生 `connection.admit()` 重新验证请求凭证；无法核实浏览器身份的桌面 IPC 连接会安全拒绝，请使用已登录的父实例网页，或让助手通过原工具审批启动。不修改 DSH 底层能力来绕过这个限制。

MAGI 工作台（以及保留的兼容面板）只在父实例的**右侧主区域嵌入子 Host 的原生对话页面**，父实例地址、原生导航和其他会话保持不变。通过已认证的 transport POST 请求取得子节点专用嵌入登录地址，再加载独立 iframe。嵌入入口由原生 Connection 校验登录令牌，保留签名、有效期与 HttpOnly，仅将 Cookie 设为 `SameSite=None; Secure; Partitioned`，解决桌面 `file://` 父页面的跨站 Cookie 限制（Chromium 将 loopback 视为可信来源）；独立打开仍使用原生 `SameSite=Strict` Cookie；不再让父页面访问需要认证的跳转路由。子页面通过官方 `shell.overlay` Slot 添加自有条目，不再替换或卸载原生侧栏及其后代功能席位。仅在嵌入模式挂载隐藏标记和声明式 CSS：以原生右列标记定位实际三栏容器，而不假设 Slot 包装层级；左栏网格项退出布局，主内容跨越左、中两列，同时隐藏残留的左侧拖拽柄和重新展开控件。不使用偏移裁剪、不修改官方插件文件，保留原生右侧栏、浮层及底部区域。固定主会话导航在浮层实际挂载、Session 与工作区目录均就绪后才调用原生 `openSession`；随后核实该 ID 的 `mainView` 引用和 binding，并等待会话历史 `openState: open`，才移除加载遮罩和一次性 URL 标记。原生导航抛错、引用未建立或历史打开失败时显示错误及显式重试按钮，不把新会话页面当作成功、不创建替代会话。样式仅在子页确认为嵌入模式时挂载，离开或卸载即移除；独立打开子页面时仍显示完整原生界面。此适配面向当前 0.2.1-rc.3 的三栏布局，官方容器结构升级后需复核。iframe 不允许顶层导航。认证链接不进入节点状态 JSON、工具结果或模型上下文；助手通过 `eva` 工具发起的启动和停止仍请求原生单次审批，用户按钮唤醒独立校验浏览器身份。

打开过程中，父面板始终显示同一位置、同一主题背景的加载提示；iframe 保持实际布局但透明且不可交互，避免先露出子页面默认界面、侧栏收起或会话切换的中间画面。子页在固定会话就绪后的渲染提交再经过两次动画帧回调，才发送就绪消息；父面板核对 iframe 窗口、精确 origin 与主会话 ID 后一次性显示。该通道只传递会话 ID 和状态，不传认证地址；失败时露出子页错误及重试，未收到消息的可见等待超过 30 秒则提供父面板重试，后台标签页暂停计时。子页重载或重试重新遮罩，卸载清理监听、计时器和动画帧；不使用固定延时强行显示。

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

## 新对话默认 Creator

EVA 的启动 overlay 将子实例 `agent-preset-registry` 的部署默认设为 `cordis`（UI 中的 Creator），不修改主实例 profile。首次固定主会话及之后新建会话使用有效默认预设。用户在子实例设置中已保存的默认选择优先；对话显式指定的 preset 也优先。已有对话不会自动变成 Creator，已进行首轮的对话不能直接切换 preset。

Creator 自带 `plugin_manager` 工具；但它不等于自动提权。当前版本的工具操作仍请求 `danger-full-access` 审批，工作区修改权限下需单次批准；审批拒绝或没有审批通道时不会执行。

启动探针从子实例的 `agentPresets.resolve()` 读取有效默认值并检查预设可用性，工具结果中的 `defaultAgentPreset` 是实际值；用户保存的其他默认值不会被强行改回 Creator。

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

0.6.15 的本地自动化测试共 236 项，覆盖凭据路径解析、拒绝非官方 provider / 禁用监视、启动 overlay 与旧文件保留、子 provider 核验及失败锁清理，增加原生 Codex 路由、模型 catalog 与就绪失败回归，并保留目录隔离、固定会话、标题、工作区与嵌入就绪回归。工具说明新增共享副作用提示；说明字符数 1048 → 1114，按字符数 / 4 粗估常驻提示增加 17 tokens（非模型 tokenizer 实测）。

[真实 runtime 集成测试](<test/integration.js>) 使用隔离的临时 `HOME` 与应用的 Electron Node-mode，参数为安装锚点与测试调用目录；覆盖审批拒绝不分配目录、自动编号、旧目录与历史保留、重复启动、HTTP 401、认证视图、停止、重启和卸载清理。Host context / approval / 父 provider 发现边界是测试夹具；子 Host 与有效 provider 是实际服务。

[共享凭据集成测试](<test/integration-credentials.js>) 自行建立临时 homeRoot 和不存在的合成凭据路径，启动两个实际 DSH 子进程；测试插件仅用固定的合成记录，通过 IPC 布尔断言核验双向更新、删除传播与 20 次并发 read-modify-write 无丢失，并确认旧子凭据文件字节不变。等待官方变更事件后再检查缓存，不以写入 ACK 当作其他进程已刷新。测试结束停止子进程并清理已核实的临时目录，不读取真实凭据、不进行实际账号登录、退出或模型请求。

在仓库根目录运行共享集成测试（ASAR runtime 需要 Electron Node-mode）：

```sh
ELECTRON_RUN_AS_NODE=1 '/Applications/MAGI.app/Contents/MacOS/MAGI' --expose-internals \
  apps/eva/test/integration-credentials.js \
  '/Applications/DeepSeek Harness.app/Contents/Resources/app.asar/dsh/package.json'
```

真实 runtime 测试**不等于当前桌面已安装或加载新版本**；安装状态需另行检查，面板可见性、跳转与实际会话选择需在当前页面验证。实际工具状态以会话中的工具定义及 `eva` 调用为准，不把旧路径、PID 或端口当成当前状态。

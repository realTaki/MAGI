# EVA

DSH Host + Web Client 插件，提供模型工具 `eva` 与每个节点独立的侧栏条目（包括离线节点），管理额外的无桌面窗口 DSH 进程。目标环境为 DeepSeek Harness Desktop 0.2.1-rc.3 / macOS。当前插件版本 0.6.6。

## MAGI 重构起点

本目录已用 DSH Host 插件替换 MAGI 旧的 EVA launcher，作为逐步改造成 DSH 版本的起点。其他旧模块和根级构建流程尚未改造，不能据此认为整个 MAGI 已兼容 DSH。背景、验证边界和后续工作见 [交接文档](<../../DSH-HANDOFF.md>)。

## 安装与更新

1. 左侧「插件」→「添加插件」。
2. 输入本插件目录的绝对路径：`/Users/shichuan/Documents/deepseek-harness/default-workspace/MAGI/apps/eva`。
3. 点击「安装」，完成后「立即启用」。

也可在新的 Creator 会话用 `plugin_manager install_bundle` 安装本目录。入口为「插件」→「添加插件」右侧箭头→「让 Agent 创建插件」；不能将已开始的会话切换成 Creator。

不要手动修改主实例 profile，不要在 profile 内运行包管理器。更新已链接安装的 Host 源码后，可先在 Plugin Manager 关闭再启用 EVA：支持 HMR 的 profile 会比较源码、清除相应模块缓存并重新加载；仅在返回 `restart-required` 时重启主实例。仍需通过实际工具调用确认新代码已经加载，不能把磁盘更新或显示的新版本号等同于当前进程已更新。本插件无需构建或系统 Node/pnpm/dsh 命令。

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

## 独立 EVA 条目与固定主会话

侧栏显示每个已知节点的独立条目，例如 EVA-000 · 在线、EVA-001 · 离线；不再提供统一 EVA 管理页面。节点停止后保留条目；父 Host 重启后从持久化标记重新发现，刷新间隔 3 秒。启动中、停止中和异常退出分别显示状态，离线图标弱化。点击离线条目只显示状态及数据目录，不请求子页面、不自动启动；需要时通过 `eva start` 和原生单次审批启动已有 handle。运行节点仍通过官方 Session Controller 创建或复用一个固定主会话，持久化其 ID。

点击某个 EVA 条目，只在父实例的**右侧主区域嵌入子 Host 的原生对话页面**，父实例地址、左侧导航和其他会话保持不变。通过已认证的 transport POST 请求取得子节点专用嵌入登录地址，再加载独立 iframe。嵌入入口由原生 Connection 校验登录令牌，保留签名、有效期与 HttpOnly，仅将 Cookie 设为 `SameSite=None; Secure; Partitioned`，解决桌面 `file://` 父页面的跨站 Cookie 限制（Chromium 将 loopback 视为可信来源）；独立打开仍使用原生 `SameSite=Strict` Cookie；不再让父页面访问需要认证的跳转路由。嵌入时，子页面通过官方 `sidebar` Slot 暂时省略自己的左栏并打开固定主会话，不裁剪 DOM、不修改底层会话或工作区能力；独立打开子页面时仍显示完整原生界面。iframe 不允许顶层导航。认证链接不进入节点状态 JSON、工具结果或模型上下文；启动和停止仍由 `eva` 工具请求原生单次审批。

固定主会话映射保存在节点的 `eva/main-session.json`。首次创建前先保留身份，再创建、检查并刷新持久化；重启只检查和复用同一 ID，不因点击面板重复创建。映射损坏、会话缺失、身份不匹配，或创建前保留记录遗留但找不到对应会话时，均拒绝启动，不自动替换或删除记录；需人工检查映射及 Session 存储。

## 同构目录与隔离

相对于主机用户目录，默认布局如下，不再嵌套在调用工作区内：

```text
~/.dsh/                                      # 主实例数据根，EVA 不修改
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

**不复制主实例凭证；启动验证不等于模型调用验证。** 迁移会保留子实例自己的已有数据与凭证。全局技能（如 `DSH_AGENTS_HOME`）、同一 OS 用户和应用 runtime 仍可能共享；这不是容器、OS 沙箱或安全边界。子进程诊断不进入工具结果，避免认证 URL 或第三方密钥进入模型上下文。

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

0.5.1 的本地自动化测试共 151 项，覆盖根工作区、旧目录及历史字节保留、固定主会话工作目录升级与失败恢复。真实 runtime 集成测试使用隔离的临时 `HOME` 与应用的 Electron Node-mode，参数为安装锚点与测试调用目录；覆盖审批拒绝不分配目录、自动编号、单根工作区、根目录工作区、旧版注册目录及历史保留、重复显式启动、HTTP 401、停止、重启和卸载清理，并检查固定主会话映射与认证视图路由。Host context / approval 是测试夹具，真实 runtime 测试也**不等于当前桌面已安装或加载新版本**；安装状态需另行检查，面板可见性、跳转与实际会话选择需在当前页面验证。实际工具状态以会话中的工具定义及 `eva` 调用为准，不把旧路径、PID 或端口当成当前状态。

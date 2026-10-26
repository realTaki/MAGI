# MAGI → DSH 重构交接

## 当前补充：MAGI Shell

- MAGI workbench 与原父实例 Main Bot companion 已合并为 [MAGI Shell](<apps/magi-shell/README.md>)，包名 `@local/magi-shell`。当前 profile 已通过官方 Plugin Manager 安装、启用新 bundle，旧两个 bundle 已移除；旧源码目录也已合并清理。
- [Host 入口](<apps/magi-shell/index.js>) 只负责当前 Host 的固定主机器人初始化；[Client 入口](<apps/magi-shell/client.js>) 同时提供 MAGI 导航与主机器人原生会话适配，只注册一个 Client 插件。聊天、工作区、Session 与历史仍复用 DSH，不再单独安装 Main Bot 插件。
- EVA 继续作为 [独立节点管理插件](<apps/eva/README.md>) 保留。MAGI Shell 在 EVA 未安装时也能使用主机器人；此次迁移未重载 EVA。迁移前后核对既有节点 PID、origin、workspace UUID 与主 Session 均不变。
- 原 `eva-parent-main` 身份目录、`parent-main` 命名空间、`/api/eva/parent-main` 和 handle `dsh-main` 均有意保留。主机器人已有 Session ID、工作区与历史复用，不因插件重命名迁移或新建。旧父框架 hash/query 标记与握手协议已移除。
- [根测试发现脚本](<scripts/test.mjs>) 与 [workspace 锁元数据](<package-lock.json>) 已接入新包。合并阶段曾有 80 项 MAGI、316 项联合 EVA 回归通过；随后 iframe 入口版本曾有 113 项 MAGI、349 项联合回归通过。这些均为历史版本结果，不能作为下述原生导航版本的验证数量。

### 当前修正：保留左右布局，直接复用 Desktop 原生主会话

- 用户明确选择保留 MAGI 左侧 Bot / 右侧聊天布局，但当前 Desktop 自己的主会话不应再嵌入第二套 DSH。先前 iframe 方案虽修复 `file://` URL 拒绝，实际仍报告“正在打开主会话…”，并未完成真实桌面打开验证；现已从源码删除该方案，而非继续补丁。
- [Client](<apps/magi-shell/client.js>) 仅在 MAGI 打开时通过条件 `sidebar.workspaces` 注册替换工作区浏览区为 Bot 列表；返回 DSH 时销毁该注册，恢复原生工作区列表。原生 sidebar 外壳、设置、主区域、Conversation、历史、输入与布局继续挂载；不再使用全屏 `shell.overlay` modal。
- 点击主机器人只清除辅助 panel 选择，再调用当前 Desktop 的 `uiWorkspace.openSession(existingMainSessionId)`。会话的加载、错误、retain 与历史全部交回原生功能；不再加自定义就绪门槛，不新建、fork 或替换主 Session，不创建第二个 Client/Host。
- 点击在线 EVA 使用 `layout.selectPanel('eva:' + handle)`，选择 EVA 已有主区域 panel。MAGI 不再自己嵌入/管理 EVA iframe；原 EVA panel 的认证、加载与草稿行为保持其原有所有权。此次改动不重启、不卸载、不重载 EVA，不改变节点身份。
- [Host](<apps/magi-shell/index.js>) 保留原身份初始化与只读清单接口；已删除旧父 iframe 的 main-open/embed 认证路由、分区 Cookie 适配及配套 helper/test。原身份辅助代码未修改，持久化身份文件不迁移。包分发与回归断言相应更新。
- 当前原生导航版本已使用 MAGI 自有 Node 完成联合回归：**301 通过、0 失败**，Host/Client 语法检查通过。旧 modal/iframe 布局测试已替换为原生浏览区、折叠栏和无加载遮罩约束。
- 已通过 Plugin Manager 仅移除后重装链接的 `@local/magi-shell@0.1.0`，两个操作均返回 `application: applied`；没有重载 EVA。连接页面的 live Slots 已确认 MAGI brand/footer 注册有效、旧 MAGI overlay 消失、原生 `conversation` 与已有 `eva:eva-000` main panel 仍保留。条件 Bot 浏览区需要点击 MAGI 后才注册。
- 应用后复读固定身份记录，仍为 `session-899d4db2-b59b-491a-80b7-00473082fb15`。`eva status eva-000` 仍为原 PID `22564`、origin `http://127.0.0.1:57355`、workspace UUID `a2a8bde2-134b-470b-9b85-958ea039b846`、主 Session `session-955a2fee-7c8e-4a5e-9cc9-406ca72e2a7e`；均未替换。
- 无可用浏览器操作/截图工具，尚未实际点击当前 Desktop 的 MAGI → 主机器人，也未验证真实历史、输入、键盘或明暗主题渲染。请在现有页面刷新后执行该交互；安装成功、测试通过与 live Slot 注册均不能冒充实际桌面打开成功。

**以下为首轮 EVA 目录替换的历史记录。安装状态、凭据共享等后续行为，以当前插件说明与上述补充为准。**

## 用户意图与本次范围

用户决定逐步把 MAGI 重做成基于 DeepSeek Harness（DSH）的版本。当前只做第一步：删除原有的 [EVA 应用目录](<apps/eva>) 内容，换入已开发的 DSH Host 插件。不要把这次目录替换描述为整个 MAGI 已迁移完成。

- 插件位置：[apps/eva](<apps/eva>)；包名 `@local/eva`，版本 `0.4.0`，ESM，无需编译。
- 已移除该目录原有的 TypeScript launcher、入口、配置和旧测试，换入插件源码、图标、语言文件及可复现测试。
- 没有复制本机 `.DS_Store`、历史 PID/端口/绝对路径的测试结果 JSON，也没有保留旧目录备份。
- 其余旧 MAGI 模块未删除或改造；没有安装依赖、更新锁文件、提交 Git 或修改当前 DSH 的插件安装来源。
- 仓库外的原插件目录目前保留，避免破坏此前已链接安装的插件。后续以本仓库插件为重构起点，不要长期维护两份分叉源码。

## 已确认的核心设计

1. **节点是独立 DSH 进程，不是 Session。** 每个节点有自己的 DSH_HOME、profile、存储和工作区，不弹桌面窗口；只绑定 loopback。不是操作系统安全隔离边界。
2. **自动命名由插件负责。** `eva start` 未提供 handle 时，插件只扫描 home 直属目录，原子分配 `eva-000`、`eva-001` 等；不要让 Agent 递归扫描用户目录或自行分配编号。每次无 handle 的 start 都会创建一个新节点；复用节点必须传回原 handle。
3. **单根目录，便于未来 Docker 挂载。**

   ```text
   ~/.dsh-<handle>/                 # DSH_HOME，同时也是子进程 cwd
     profiles/<handle>/
     workspace/                    # 预先创建并注册，但不是 cwd
     storages/
     eva/
   ```

   必须保留已注册的 workspace 文件夹。注册工作区不代表创建 Session，也不替用户选择 Session 的工作区。
4. **默认 Creator 是新 Session 的预设。** 启动 overlay 为 `agent-preset-registry` 设置 `default: cordis`；UI 名称为 Creator，内部 ID 不是 `creator`。用户已保存的默认选择优先；已有 Session 不会自动切换，已开始交互的会话也不能直接切换预设。子实例默认值不影响主 DSH 的当前对话。
5. **权限与凭据独立。** 不复制主实例凭据；保留子实例已有配置。EVA start/stop 需要单次批准；status 不需要。Creator 提供 `plugin_manager`，但不会自动提权；其操作仍受 `danger-full-access` 及审批规则约束。
6. **插件生命周期管理子进程。** 停用插件或退出父 Host 会停止它管理的子节点。停止节点保留数据，不自动清理目录来回收编号，也不按磁盘上的 PID 接管其他进程。
7. **目录迁移安全边界。** 现有自动迁移只针对权威 registry 中可验证、没有会话历史的旧工作区，保留 UUID，核验复制结果后删除原目录，不保留备份。冲突、符号链接、历史数据、未完成标记等情况必须拒绝启动，不要为了成功率放宽检查。旧版离线迁移工具另有保留原目录的行为，详见插件说明，不能混为一谈。

## 实现导航

- [插件说明与安装](<apps/eva/README.md>)：工具协议、目录、迁移和生命周期。
- [入口与审批](<apps/eva/index.js>)：注册 `eva` 工具，执行 start/status/stop。
- [运行时管理](<apps/eva/runtime.js>)：分配 handle、持有 owner lock、生成启动 overlay、拉起与回收进程、校验 ready 消息。
- [子进程探针](<apps/eva/child.js>)：验证 home/cwd/profile，注册 workspace，并从 `agentPresets.resolve()` 报告有效默认预设。
- [执行包装](<apps/eva/runner.js>)：子进程执行与父进程退出清理。
- [工作区迁移](<apps/eva/relocate-workspace.js>)：注册信息、内容校验、原目录删除及失败隔离。
- [旧版离线迁移](<apps/eva/migrate-layout.js>)：历史布局辅助工具，不是新的主启动入口。
- [回归测试](<apps/eva/test>)：运行时、迁移、安全拒绝路径及真实 DSH 集成测试入口。

## 测试与验证边界

本次已在新目录使用 MAGI 安装包自带的 Node `v24.21.0` 运行全部回归测试：**80 通过，0 失败**。不使用系统 Node，不修改 PATH 或系统环境，不全局安装工具。

从仓库根目录运行：

```sh
'/Applications/MAGI.app/Contents/Resources/runtime/bin/node' --test apps/eva/test/*.test.js
```

准备好源码自带 runtime 后，也可使用仓库自身运行时：

```sh
apps/shell/runtime/bin/node --test apps/eva/test/*.test.js
```

本次检查时，仓库自身 runtime 尚不存在，因此使用已安装 MAGI 的 runtime。遵循 [仓库运行时要求](<AGENTS.md>)；缺少运行时应报告或修复，不要静默改用系统 Node。

此前同一插件实现已经在 DeepSeek Harness Desktop `0.2.1-rc.3` / macOS 上验证：真实子进程启动、根目录 cwd、嵌套注册工作区、默认 `cordis`、关闭插件后回收进程，以及 Plugin Manager 关闭再启用后加载新实现。原有 `eva-000` 的 workspace UUID 为 `111e2849-79ba-44e9-9c8d-20a11c80f995`，此前迁移和更新时保持不变；这不是新节点应使用的硬编码 UUID。

**这些历史实机验证来自仓库外原安装路径。** 本次未把当前安装重新链接到本仓库，也未从本仓库路径重新做真实 Host 集成启动；不要把源码复制和单元测试通过说成当前 DSH 已切换安装来源。此前未验证模型调用，也未实际执行子实例的 `plugin_manager` 工具。HTTP 401 只证明服务要求认证，不代表模型或工具调用成功。

## 如何接入当前 DSH

插件安装目录现在应指向：

```text
/Users/shichuan/Documents/deepseek-harness/default-workspace/MAGI/apps/eva
```

通过官方 Plugin Manager 安装/更新本地 bundle，不手改主实例 profile 或在 profile 内手动跑包管理器。已有同包名链接应通过官方管理流程切换，不并行启用两份管理器。

源码更新后可先停用再启用插件；**不应一概要求重启整个 DSH**。停用会停止其子节点，应先告知用户。只有管理器明确返回 `restart-required` 才要求重启主实例。重载后需实际调用工具确认新代码加载，启动节点后核对 `cwd`、workspace UUID 和 `defaultAgentPreset`；不要只看文件或版本号。

若新的主 DSH 对话需要 `plugin_manager`，新建对话并明确选择 Creator。修改子实例默认预设不会给已有的主实例对话追加工具。新对话也不会自动完整继承旧聊天；请先读本文与插件说明。

## 尚未改造的旧 MAGI 集成

- [根级 TypeScript 配置](<tsconfig.json>) 已移除旧 EVA 项目引用；新插件无需编译，不补回旧配置。
- [根级测试脚本](<scripts/test.mjs>) 已直接收集插件源码 `.test.js`，不再寻找旧 EVA 编译测试；其余包仍按当前 TypeScript 源文件收集编译输出，避免运行已删除测试的残留产物。
- 三项旧 Shell 测试已直接从仍保留的 `@magi/launcher/composition/agent.js` 导入 `Magi`，绕过被删除的同名 EVA 转导出文件；不改变旧 composition 的行为或宣称它已改造成 DSH。`atomic-launcher-state` 测试仍依赖已删 helper，旧 launcher 启动／集成路径也尚未迁移，不删除或跳过这些失败来假装整仓通过。
- 依赖锁已同步新插件 workspace 元数据。根级 npm 脚本、Shell、其余包及旧架构文档仍大多属于原实现。原来依赖 EVA launcher 的导入、打包与启动流程还需梳理；这些静态入口修正不代表旧 Shell、整仓测试或 DSH 集成已通过。

不要为了让旧整仓构建表面通过而补回旧 EVA launcher，或把旧架构继续塞进新插件。

## 后续建议（尚未执行，需按用户节奏推进）

1. 先确认新的 MAGI 与 DSH 的职责边界：哪些能力直接使用 DSH，哪些需要做成 Host/Client 插件。不要预先假定所有旧包都必须保留或立即删除。
2. 经用户同意，把当前 DSH 的本地插件安装来源切换到本仓库，再验证真实启动、停止和默认预设。
3. 分步调整根级 workspace/锁文件、构建测试入口与旧 Shell 接入，单独记录每次删除和兼容范围。
4. 在现有单 DSH_HOME 布局基础上设计容器化；当前代码仍明确面向已安装的 macOS DSH Desktop，不宣称已经支持 Docker 或 Linux。
5. 再逐步迁移 MAGI 的其他功能。本次没有主动启动重构、下载新依赖、启动新 GUI 服务或改动用户运行数据。

下一次 Creator 对话可这样开始：

> 先读 MAGI 的 AGENTS.md、DSH-HANDOFF.md 和 apps/eva/README.md。我们已经用 DSH Host 插件替换旧 EVA，接下来逐步重构 MAGI；先确认现状和下一小步，不要直接大规模改写，也不要复制主实例凭据或递归扫描用户目录。

# 插件分层与安装归属 / Plugin layout

当前支持六个 workspace。插件管理页中的**安装包**与包内**运行组件**不是同一层，不能按卡片数量计算 Bot 或记忆实例。

| 层级 | 中文 / English | 包或插件 | 职责与归属 |
| --- | --- | --- | --- |
| 产品 | MAGI 工作台 / MAGI Workbench | `@local/magi-shell` | 桌面导航、原生 DSH/MAGI 切换；附带下列主会话与能力预设组件。可不安装 EVA。 |
| 产品 | EVA 节点管理 / EVA Node Manager | `@local/eva` | 独立节点的进程、启动恢复、通道与凭据部署；附带私信、记忆及默认暂停的潜意识，子节点复用 Bot mode。可不安装工作台。 |
| 共享组件 | Bot 主会话 / Bot Main Session | `@magi/bot-mode` | 固定 Bot 身份、工作区与主会话；仅固定主会话获得前台协调策略。由产品启动流程显式初始化。 |
| 共享组件 | Bot 能力预设 / Bot Agent Preset | `@magi/bot-mode/preset-loader` | 向原生 roster 注册 `bot-mode`，不修改父端默认选择、已有会话或审批权限。 |
| 共享组件 | Bot 私信 / Bot Direct Messages | `@magi/dm` | 独立私信实现；接入方提供身份和进程通道。EVA 默认挂载它。 |
| 共享组件 | Bot 记忆 / Bot Memory | `@magi/memory` | 本 Bot 各会话共享的持久记忆。可独立安装；EVA 默认挂载同一组件。 |
| 共享组件 | Bot 潜意识 / Subconscious | `@magi/subconscious` | 默认暂停的独立根会话反思。可独立安装；EVA 挂载同一组件但不启用反思。 |

包名、稳定 Loader 行 ID 和数据目录不因显示名称变化而改变。没有新增 Agent 引擎或调度器；主会话协调是提示策略，不是硬实时保证。依赖并不自动选择其 bundle patch；EVA/Shell 显式挂载各自需要的共享组件。独立发行包仍保留这些依赖，不能为隐藏列表而删除它们。

潜意识（[`@magi/subconscious`](<../packages/subconscious/README.md>)）是独立共享组件，不属于 Shell、记忆存储或固定 MAIN。EVA 父端与子端各挂一个组件，子端排除所选 bundle 的父根，绝不以 `enable:true` 自动启用。未来显式启用后仅使用独立根 Session 的单条慢速串行对话、小轮次预算、冷却和无新输入跳过；不派生子 Agent，不承诺 24/7 模型运转。记忆仍由 memory 管理，外部通知仍由 DM 管理；原生 Schedule 负责用户任务，潜意识不建立自唤醒任务。

## 为什么记忆会出现两处？

若同时选择 EVA 与独立 `@magi/memory` bundle，两份 patch 都声明同一个 `magi-memory` 行。原生 Loader 按稳定 ID 合并，不据此建立两套记忆存储；但管理页会同时展示独立安装包与 EVA 包内组件；当前原生管理器会将重复 patch 目标标为不可寻址，无法按卡片分别控制同一个共享行。

- 只使用 EVA 自带记忆时，通常只需选择 EVA，不必再选择独立 memory bundle。
- 只需要记忆、或准备移除 EVA 时，保留独立 memory bundle。
- 不要为隐藏重复卡片禁用共享 `magi-memory` **插件行**，那可能关闭实际使用的记忆功能。
- 在审查实际版本、覆盖关系及用户用途后，可取消冗余独立 **bundle 的选择**，保留依赖和所有数据。取消选择不会删除已安装包的卡片；真正移除卡片涉及卸载，不能把二者混为一谈。

## 为什么源码有组件，页面却显示“关闭”？

原生管理页从磁盘读取 bundle 的声明，又从正在运行的 Loader 获取状态。源码链接包已经修改、Host 尚未重新装配时，新增声明没有对应的运行条目；当前 UI 会将缺席的条目投影为“关闭”；这不代表用户有意禁用了组件。原始包名和默认图标则可能来自运行时解析表尚未更新，也可能是子路径资源导出缺失，需分别核对。

子路径插件需独立导出自己的 locale/icon。`preset-loader` 已提供中英文资源，并复用 Bot mode 的原始图标；无需激活插件或创建会话来读取元数据。相关回归见 [Bot 包契约测试](<../packages/bot-mode/test/package.test.js>) 和 [真实发行包测试](<../scripts/test/pack-dsh.test.mjs>)。

## 更新与验证边界

1. 先检查运行清单、磁盘 patch、选中的 bundle 与实际版本，不以卡片或普通 Node 脱离 Host 的包查找结果代替运行状态。原生启动器根据选中 bundle 的依赖图建立运行时解析表。
2. 源码测试与打包通过不代表当前 GUI 已更新。使用原生插件管理操作，在获得允许中断的维护窗口后重新装配；已安装本地链接包不能靠反复安装同一路径来更新。
3. 全 profile 重新装配可能同时替换已变化的 EVA 及其依赖。EVA 卸载会停止其管理的节点；即使仅取消独立 memory bundle 的选择，也不能承诺零中断。不要擅自重载、停启节点或编辑 profile 文件。
4. 操作结果以原生 `application` / `warnings` 为准，之后检查 Loader 状态、单一有效共享行、名称/图标资源与 GUI 刷新后的实际展示。若无浏览器控制，只能确认资源和运行条目，不能声称已目测渲染。
5. 此整理不迁移会话、记忆、凭据或已有预设，不启动新 Bot，也不启用待开发的其他记忆后端。

English summary: two product bundles (Workbench and Node Manager), five shared component rows (including paused Subconscious). Preserve standalone product behavior and stable row IDs. A redundant standalone Memory selection is a duplicate patch source, not evidence of two stores. Reconciliation can stop EVA nodes; obtain a maintenance window before changing the live profile. Source/package verification is not live visual verification.

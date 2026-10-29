# MAGI → DSH 重构交接

## 当前补充：MAGI Shell

### 最新：bot-mode 完整职责拆分（仅源码，未部署）

- 在首个身份提取提交 `45983847` 上继续完成通用 Agent 预设所有权：唯一能力清单移到 [bot-mode 原生声明](<packages/bot-mode/presets/bot-preset.yml>)，插件正文与此前 EVA 快照保持相同；EVA 仅通过[包内 helper](<packages/bot-mode/presets.js>)生成 `bot-mode` + 旧 `eva` 两个原生声明，不做 YAML 普通反序列化、不丢 `!!js` 与隔离配置。不是别名/继承，两声明由原生 registry 分别管理。
- bot-mode 管固定主身份和通用能力；EVA 保留进程、home/profile/端口、IPC、原生 Codex/凭据部署与启动检查；Shell 管导航，只添加父 roster 不改父默认。子 overlay v6 只将部署 fallback 改为 `bot-mode`；保存默认值、会话明确选择及历史 preset ID 不迁移。兼容 `eva` 长期保留，重启由原生按记录 ID 恢复当前声明，不声称历史 revision 永久固定。
- 独立 bundle 与 Shell 使用共同身份/预设根 ID；原生 Loader 同 ID last-wins。子 overlay 禁用选中父根后挂自己的声明，避免重复注册。包内 loader 通过自身模块 URL 定位 YAML，通过原生 installation anchor 加载 Include，避免扁平 patch 的 Host baseUrl 或依赖提升假设。Include 和声明包登记 optional native peers，兼容原生 source-linked resolver。
- 最终 MAGI 自带 Node 验证：完整 **612/612**、portable **152/152**，0 失败/0 skipped；manifest/resources 与 **36 个源码模块**通过；真实独立 bot-mode、EVA、Shell tarball 解包/入口 apply/资源闭包通过。独立只读复核未发现 must-fix。EVA 工具说明只纠正共享预设名称，字符 **1169 → 1169**；未实测实际完整首轮 prompt tokenizer，不把字符数当 tokens。
- MAGI Electron Node-mode [隔离原生 probe](<apps/eva/test/preset-native.js>) 通过：原生 schema 两文档解析、Creator 初始 23 顶层/30 模块 parity/import、技能目录、源码链接目录解析、真实 package loader → Include → declaration 注册、父重复 ID 去重、子禁用父预设根。真实 registry 用空 synthetic 能力正文验证未保存默认 → `bot-mode`、保存 `eva`/其他默认保持，以及记录 `eva` projection 的 native scope 绑定。
- probe 无 Host 启动、无文件/profile 写入；**未验证完整真实能力 eager mount 或持久 Session reopen**，不冒充模型调用、GUI 验收或线上部署。既有生命周期/身份恢复仍由完整 fixture 回归覆盖。未安装、重载、推送或重启运行 Bot。

### 前次：bot-mode 第一增量（仅源码，未部署）

- 新增独立 [@magi/bot-mode](<packages/bot-mode/README.md>)，抽出父/子固定主会话身份；Shell 和 EVA child 消费同一个 Host 能力，EVA 父启动器不挂父身份根。固定 Session ID、父 profile 状态 namespace、子 home 状态路径均不改；严格父 v2 策略与子 v1 同 ID 升级保持分离，持久化 helper 与原实现逐字一致。
- `magiBotMode.initialize()` 只在明确启动调用时变更状态，single-flight，成功后缓存；`magiMainSession.current()` 保留只读 ABI。Shell 在 appReady 调用，EVA 在 home/cwd/profile、凭据、Codex 和 preset 检查后调用。GET 不触发恢复，卸载不再发布身份；新增并发、失败重试、卸载中 flush、配置与重复 owner 回归。
- EVA 仍管进程、home/profile/端口、IPC 和嵌入；Shell 管导航和只读清单，DM/memory 独立。本步**未搬 Agent preset**，不改权限、工具集、保存默认值或历史会话；下一增量再逐项权衡通用 Bot 配置与 EVA 专属配置。
- 子 overlay v5 用已安装 bot-mode 绝对入口，并禁用选中 Shell/独立 bundle 的父根；入口纳入不可变 startup hash，旧 patch 不重写。第五 workspace、锁文件、两应用 bundled dependency、资源检查/collector/打包均接好；真实独立包和两应用内嵌包经过解包、资源/exports/import 闭包回归。
- MAGI 自带 Node：完整 **608/608**、portable **148/148**、源码/resources **34 模块**通过，0 失败/0 skipped；独立只读复核未发现新增 must-fix。均为当前 macOS fixture/源码验证，不代表 Windows Host 或真实 GUI 验收。
- 未安装/重载运行 profile，未启动、停止或重启 Bot，未部署或推送；源码完成不等于当前运行实例已更新。

### 前次：补齐插件自带操作 skills（分步，仅源码）

- EVA 新增原生 `magi-eva-nodes` [操作指引](<apps/eva/skills/eva-nodes/SKILL.md>)，覆盖节点选择/复用/新建、审批、共享凭据边界、状态语义与安全失败恢复。仅父 Host 注册；有 child 角色标记时在注册前返回。
- 通过可选 `skills` 注入注册，资源锚定安装模块，随服务 scope 注销/恢复；不增加核心硬依赖，不改工具 schema 或审批。manifest 包含完整资源；回归覆盖服务缺席/卸载/恢复、child 边界以及真实归档在离线独立 consumer 中读取并注册 skill。
- EVA 增量使用 MAGI 自带 Node：完整回归 **586/586**、portable **134/134**、源码/resources **30 模块**通过，0 失败/0 skipped；当前 macOS 结果不代表 Windows 实机验收。
- memory 接续新增原生 `magi-bot-memory` [操作指引](<packages/memory/skills/bot-memory/SKILL.md>)：先查重、包含归档项核对、按版本更新、冲突/不确定写入后重读、归档恢复与明确授权删除。同样使用可选 scope 注册，缺席/重载不影响工具和已有 memory context，卸载不删除记忆；独立 memory 归档及 EVA 内嵌 memory 均验证安装后的实际入口 apply、资源内容与注销。
- memory 增量使用 MAGI 自带 Node：完整回归 **588/588**、portable **136/136**、源码/resources **30 模块**通过，0 失败/0 skipped；均在当前 macOS 执行，不代表 Windows 实机验收。
- memory skill 描述当前 SQLite 工具，不激活单 Markdown 后端、不改变分类/容量/上下文布局，不导入历史数据库。两个增量分别提交，下方旧“只有 DM”的说明是当时状态。未恢复集中式 BUS skills；不改原生/用户 skill、preset、profile 或凭据，不安装/重载插件，不重启 Bot。源码更新不代表运行实例已生效。

### 前次：删除旧 BUS MCP（仅源码）

- 按用户要求删除 `packages/mcp` 的 worker、`mcp_server` 控制工具、manifest 和 TypeScript 配置，共 4 个源码文件。该模块不在当前四个 workspace、根 lock、打包或运行闭包中；无需安装依赖或改锁，历史源码保留在 Git 历史。
- MCP 能力归属原生 DSH；本次不增加、删除、连接、断开或修改任何原生 MCP 服务器，不修改 preset 或活跃工具 schema，不读写服务器凭据、不重载插件或重启 Bot。旧 BUS 的 MCP 表结构与 Drizzle 历史作为归档保留，用户既有数据库及配置不删除、不读取、不迁移。
- [依赖策略回归](<scripts/test/dependency-policy.test.mjs>)新增 MCP 旧目录/当前插件依赖/lock 禁止恢复约束，修正归档工具所有权注释并更新仓库边界；[旧 Feed 设计](<docs/shell-feed.md>)补归档提示，避免将已删除的 MCP worker 管线当成当前功能。当前原生插件实现保持不变；不把源码回归测试描述为真实 MCP 连接验收。
- 使用 MAGI 自带 Node 验证：完整回归 **584/584**、portable **132/132**、源码/resources **29 模块**通过，0 失败/0 skipped；在当前 macOS 执行，不代表 Windows 实机验收。

### 前次：删除旧集中式 BUS skills（仅源码）

- 按用户要求删除 `packages/skills`：旧 worker、`load_skill` 工具、manifest、TypeScript 配置及六份历史 builtin skill，共 10 个源码文件。该模块已不在当前四个 workspace、依赖锁、打包与原生运行闭包中，无需迁移配置或安装依赖；历史源码可从 Git 历史恢复。
- 原生 DSH skill-filesystem / skill 工具及官方 preset skill 资产不变，DM 插件自带 skill 保留；不删除用户工作区曾由旧 worker 复制或编辑的技能文件。当前仓库只有 DM 已有自带 `SKILL.md`；EVA 和 memory 的专用操作 skill 尚未补齐，Shell 是 UI-only，不能把本次删除描述成所有插件的 skill 迁移已经完成。
- [依赖策略回归](<scripts/test/dependency-policy.test.mjs>)新增旧 skills 目录/当前插件依赖/lock 禁止恢复约束；修正归档工具和 contacts 的过时所有权注释。归档 BUS registry、旧 agent prompt 和 subconscious allowlist 中的历史 `load_skill` 契约保留，不恢复旧实现。下方此前“暂不删除旧 skills”记录描述当时状态，已被本节取代。
- 没有修改当前插件实现、preset、用户 profile、数据库、会话或凭据，没有安装/重载插件或重启 Bot。本次只删除已退出运行链路的旧资源，不改变活跃工具 schema 或模型 system prompt，也未发起真实模型调用。
- 使用 MAGI 自带 Node 验证：完整回归 **583/583**、portable **131/131**、源码/resources **29 模块**通过，0 失败/0 skipped；均在当前 macOS 执行，不代表 Windows 实机验收。

### 前次：删除旧 BUS launcher（仅源码）

- 按用户要求删除 `packages/launcher` 整个旧模块（runtime、composition、worker、manifest、TypeScript 配置和说明）。当前 EVA 的 DSH 节点管理保持不变；旧 launcher 不在四个当前 workspace、根 lock 或打包/运行闭包中，无需改 lock 或安装依赖。
- [依赖策略回归](<scripts/test/dependency-policy.test.mjs>)新增 launcher 目录/当前插件依赖/lock 禁止恢复约束；旧 channel 与 [memory 删除回归](<scripts/test/memory-archive.test.mjs>)不再读取已经删除的 launcher 文件。更新贡献指南、归档架构链接及仓库范围说明。
- 其他归档 BUS、工具和 webapp 源码中对旧 launcher 的引用仅作为历史保留，不承诺可编译或运行，不伪造替代接口。下方此前“仍保留 launcher”的记录描述当时状态，已被本节删除结果取代；源码可从 Git 历史恢复。
- 保留前一步 DM 插件自带 skill 及其生命周期/分发测试改动。本次不继续 EVA/memory skill 迁移，也不提前删除旧集中 skills；没有修改用户 profile、数据库、会话或凭据，没有安装/重载插件、重启/停止 Bot。
- 删除后使用 MAGI 自带 Node 完整回归 **582/582**、portable **130/130**、源码/resources **29 模块**通过，0 失败/0 skipped；在当前 macOS 执行，不代表 Windows 实机验收。只读独立复核确认删除范围及当前依赖闭包无遗漏。

### 最新：原生 Bot memory 第一版（当前父 Host 已启用；运行中子 Bot 尚未更新）

- 用户随后授权删除旧 memory 模块及提交 GitHub：已删除旧 BUS memory 源码和过时文档，清理归档 launcher 的依赖、project reference、worker 注册与 hosted-worker 名单。历史源码可从 Git 历史恢复；旧数据库不删除、不读取、不迁移。
- 单文件下一版仅完成[Markdown 存储底座](<packages/memory/document-store.js>)与针对性测试，每 Bot 一个文档、不分长中短；尚未接入插件入口/工具，也未打包部署。现有运行插件仍使用下述 SQLite 与 runtime-context 机制。固定 system 后、history 前的位置需要先解决现有会话插入及所有压缩入口的保护，不能声称已上线。
- 删除旧 memory 并合并远端安全更新后，MAGI 自带 Node 完整回归 **579/579**、portable **129/129**、源码/resources **29 模块**通过，0 失败/0 skipped；均在当前 macOS 执行，不代表 Windows 实机验收。未重启或重载运行中 Host/Bot。
- 合并远端的 Electron/fast-uri/undici 安全更新提交时保留全部 Git 历史，不恢复已删除的 Electron/provider 依赖图。Electron 现检查整个产品锁中不存在该依赖；fast-uri 现检查完整依赖图不再包含该 parser，同时保留上游 parser 行为回归分支供未来有意重新引入时执行。
- 新 [@magi/memory](<packages/memory/README.md>) 是独立原生 Host 根，不加载 BUS。每 Bot 使用固定 `profileContext.home/memory/memories.sqlite`，跨该 Bot 的 Session/profile 共享、不同 Bot 隔离；不受 Session cwd 改动影响。SQLite 事务、CAS 版本、私有权限和路径检查、总条数及完整活动快照字节预算均有回归。
- [插件入口](<packages/memory/index.js>)注册 `required:true, interpolate:false` 的 `magi:memory` 动态上下文，不重写 Persona、preset 或模型请求，不使用旧验证的 `agent.inject()`。同 Host 的所有 Agent scope 在下次请求接纳时通过原生机制获得当前事实；完整自定义 system prompt 和 optional-context suppression 也保留必需记忆。旧快照可能仍留在会话历史，归档/删除并不抹除日志。
- 五个管理工具 `list_memory/get_memory/save_memory/archive_memory/delete_memory` 原生注册并接受 Agent 及原生 guard/cancellation；更新/归档/删除需当前版本。删除工具要求 `confirm:true` 并提示必须有明确人类授权，但布尔值本身不是身份或授权证明，不能声称插件机械验证了对话中的人类许可。没有自动抽取、向量检索、TTL、潜意识、后台维护或管理 UI。
- 原生 Plugin Manager 已启用 `@magi/memory@0.1.0` 于当前 desktop Host，既有主会话也自动出现记忆上下文。新[真实验收回执](<../magi-maintenance/native-memory-verification/receipt.json>)（2026-10-28T19:50:38.365Z）：两个全新独立 Session、无父会话/继承/先前聊天；三次 `openai-codex/gpt-6.1-sol/medium` 真 Agent 请求。普通新会话与 complete prompt+suppression 新会话都答对仅存于记忆的暗号，首会话下轮答出更新后的新暗号；每次五个工具 schema 均可见，但无模型工具调用。原生工具路径覆盖创建、更新、get、归档、删除，Agent guard 拒绝未获允许的写入，归档后活动 context 不含测试暗号。
- 此验证无请求改写/人工注入；只读 `llm/stream` 观察 native runtime-context，同时冷读持久事件确认记忆。只删除本次生成的 synthetic UUID，测试后当前记忆库为空；两个 handle 已释放、验证 bundle 已原生卸载，保留测试 Session 日志与回执，正式插件继续启用。此处永久清理仅针对测试夹具，不代表可以删除用户生产事实。
- EVA 源码/打包已接入第四 workspace：依赖并打包独立 memory，子 Host 通过绝对已安装入口挂独立根，对选择 standalone/EVA 两种 bundle 的既有 profile 避免重复挂载；child-host version 4 且启动 artifact hash 包括 memoryEntry。独立 memory 和 EVA 压缩包均通过脱离源码的离线安装/import 验收。**尚未重新加载父 EVA 管理器，也未重启/修改 eva-000；更新父 EVA 后再启动的子 Bot 才走新装配。不能声称当前运行的 eva-000 已有新记忆工具。**
- MAGI 自带 Node：插件 18 项、整套 **570/570 通过，0 失败/0 skipped**，portable gate **127/127 通过**（在 macOS 执行，不代表 Windows 实机）；源码/resources **28 模块通过**。测试清理顺序显式保证关闭 SQLite 后再删除临时目录。独立审阅未发现阻塞原生契约问题。真实模型验收不覆盖 GUI 视觉、fork、Schedule、live compaction 或 Host 真重启；这些只依赖原生上下文机制/持久化单测，不夸大为端到端验收。下一小步应先约定父 EVA/运行 Bot 的安全更新窗口。

### 前次：最小跨 Session 记忆注入验证（通过，临时插件已卸载）

- 用户仅授权验证，不是实现完整 memory。仓库外的一次性 Host fixture 读取[固定测试记忆](<../magi-maintenance/memory-probe/memory.md>)，在 `agent/created` 中仅对预先登记的测试 ID 调用原生 `agent.inject()`；未修改 Persona、全局/项目指令或已有会话，没有接入旧 BUS、创建数据库、提取事实或后台维护。一次性 fixture 故意省略 locale/icon，不是可分发的产品插件。
- 最终真实[验收回执](<../magi-maintenance/memory-probe/receipt.json>)（2026-10-28T19:34:30.018Z）：同一 Host/Bot 中两个 `ctx.agents.create` 独立全新 Session，无父会话/继承事件，提问前 user/assistant 历史均为 0；同一 177-byte 记忆各注入一次，SHA-256 一致。问题不含答案，两次均使用当前默认 `openai-codex / gpt-6.1-sol / medium`，没有修改默认模型配置。
- 两个真实 Agent 请求都在只读 `llm/stream` 观察点确认含测试暗号 `MAGI-MEM-7c29e14b6f03`；两次回答均精确等于该暗号，turn/end 为 completed，无工具调用。虽然基础组合呈现了 4 个工具，测试 scope 的单调 guard 禁止任何工具执行，不能声称测试 Agent 完全没有工具 schema。
- 两个 handle dispose 后再经原生 `sessionQuery.readSession` 冷读：注入 inbox splice 与消费后的 user/message 均持久存在；Agent 和 live Session 均已释放。证明的是“选定记忆进入下一次真实模型输入，并能被本次模型回答使用”，不是模型永远遵从、自动学习或任意轮次/压缩后永久保留。
- 调试记录保留：[首次接口字段错误](<../magi-maintenance/memory-probe/attempt-1.json>)、[消息缺少身份](<../magi-maintenance/memory-probe/attempt-2.json>)、[观察器误计辅助请求](<../magi-maintenance/memory-probe/attempt-3.json>)。原生 Agent 输入必须有唯一 id、role/content/source；README 的简写不能直接当完整 durable message 使用。`readSession` 返回值不能假定含 `.header`，Session header 从 live Session 读取。原生标题生成也会引用问题发起独立 LLM 请求；最终观察器按注入消息的 source 区分对话请求，避免误把标题请求视为记忆漏注入。
- 每次调试版本均先通过 Plugin Manager 移除，使用不同模块入口避免 ESM 缓存复用；最终 v4 也已原生 remove，复查 bundle 列表恢复 22 项，所有 probe 包均不存在。共保留 6 个显式测试 Session 的原生日志供审计（前两次未驱动模型，后两轮各两个），没有删除历史；无活跃测试 Agent、常驻加载器、新服务端或 Bot 重启。
- 边界：只验证这两个原生新建会话，没有验收 GUI 的默认 preset 装配、已有会话恢复、清空/压缩、Host 重启、增量注入、内容筛选或并发竞争；没有创建正式每 Bot `memory/` 目录。下一步仍须单独授权并保持小步实施。

### 前次：记忆工作准备：上下文入口与旧 memory 差距（仅调研，尚未实现）

- 用户要求后续记忆工作记住四个入口：`$DSH_HOME/AGENTS.md`（默认 `~/.dsh/AGENTS.md`，Host 级约定）、`$DSH_AGENTS_HOME/AGENTS.md`（默认 `~/.agents/AGENTS.md`，共享约定）、项目适用的 AGENTS.md 链、Agent preset 中的 Persona 配置。前三者由 agent-instructions 按适用范围/预算加载，Persona 在 system prompt 组装时应用；不是四个自动同步的记忆副本，也不是自动从聊天学习。
- 普通笔记/交接文档不自动注入。本节用于持久保存调研结果，不把它当成已部署的新记忆加载器。当前未创建全局 AGENTS.md、未更改 Persona/preset 或运行中 profile。
- 当时调查的旧 MemoryWorker 与其工具后来已按用户授权删除；[MemoryBook](<packages/bus/books/memoryBook.ts>)数据模型保留，当前 DSH 插件闭包未接入。旧存储为每 Bot workspace 的 `memories/books.db` 中 `books_memories` 表，同 Bot 跨聊天共享，不跨 Bot 自动共享；本次源码删除不删除旧数据。
- 旧版专属能力：结构化事实/偏好持久条目；list/save/archive/delete 工具；更新/归档 expected_version 乐观并发检查；每轮注入全部未归档条目；前台与潜意识共用同一本记忆。DSH 的会话历史/压缩、AGENTS.md 与 Persona 只分别覆盖历史续接、文件约定与身份设定，不能直接声称已替代该闭环。
- 旧潜意识通过周期任务驱动私有会话，按工具观察前台消息增量、读取各聊天摘要/近期消息，再由模型决定合并/保存/归档；允许 list/save/complete_memory，不允许永久删除或直接外部通信。不是每条消息后确定性提取；增量游标也是模型维护的私有笔记。原生 Schedule 能承接触发器，但隔离会话、观察范围/游标、维护策略与权限仍未迁移，旧 preset 源码本身不代表仍有可运行调度器。
- 旧实现也不是向量 RAG：没有相关度检索、自动 TTL 或算法去重；kind 仅为标签；来源/不确定性通过 detail 文字记录；去重、事实修正及归档由模型执行工具完成。没有发现现存专用 memory 管理 UI；当前回归入口不覆盖归档 memory/BUS/潜意识实现。本轮只做源码调研与文档记录，不声称执行过旧模块验收。

### 最新：删除旧 task channel，定时指令统一原生 Schedule

- 删除旧 channel-tasks 模块（worker、工具、manifest、TypeScript 配置），清理归档 launcher 的依赖/project 声明与旧工具注释。根安装图原本不包含该包，不改 lock、不安装依赖。
- 当前定时指令使用 DSH 原生 Schedule，绑定原 Session，到点恢复/驱动该会话。用户已在本会话完成真实一次性 60 秒暗号测试，按期收到 `test 1 2 3 4`；这是原生调度验收，不是旧任务数据迁移或所有周期规则的完整验收。
- 不删除旧数据库、BUS task 数据模型或历史记录，不自动迁移旧任务；需要保留的旧任务须在 DSH 显式重建。旧潜意识唤醒与启停语义不承诺等价迁移。同时移除归档 composition 的 TaskWorker 导入、实例注册及 hosted worker 名单项；不恢复旧 BUS launcher。
- 新增删除边界回归；未修改原生调度插件、EVA/DM 实现、运行中 profile、会话或凭据，未重启 Bot，也未再创建验收任务。
- MAGI 自带 Node 最终复验：源码/resources **25 模块通过**，完整入口 **547/547 通过**、portable **117/117 通过**，均 **0 失败、0 skipped**；`git diff --check` 通过。旧任务模块/注册无残留 TypeScript 导入；本次未执行旧归档构建或 Windows 实机验收。

### 最新：删除旧 peer channel，本地通信统一 DM

- 用户确认暂不考虑远程通信后，已删除旧 channel-a2a 整个目录（实现、README、配置和测试），清理归档 launcher 的依赖/project 声明及失效文档链接；根 lock 原本就不包含该包，无需重建。
- 当前只保留本地 Bot ↔ Bot DM 与 Bot → 人的 `@user` 通知，由 [DM 插件](<packages/dm/README.md>)承担。EVA 的 Host Web 地址不是远程 DM 传输，当前启动仍绑定 loopback，消息使用受管进程 IPC；不声称跨机器 DM 已完成。
- 归档 launcher 中的旧 composition 源码保留历史导入，README 明确其不可执行；不伪造替代 PeerWorker、不恢复已退役 BUS launcher。该源码不在三个当前 workspace 的安装/构建/运行闭包中。
- 增加依赖策略回归，约束旧模块确实不存在、依赖及 project 声明已移除、当前三个插件均不依赖旧 channel。未修改 DM 实现、运行中 profile、凭据或会话，未重启 Bot。
- MAGI 自带 Node 最终复验：源码/resources **25 模块通过**，完整入口 **546/546 通过**、portable **116/116 通过**，均 **0 失败、0 skipped**，`git diff --check` 通过。验证包含本地父子/兄弟 DM、离线恢复、`@user` 通知及独立包离线安装；没有重复发送真实验收消息，也不声称新增 live/Windows 验收。

### 最新：删除旧 Shell / provider，收敛当前安装图（仅源码）

- 已删除旧 Electron Shell 与 provider 模块；只安装 `apps/eva`、`apps/magi-shell`、`packages/dm` 三个 workspace。其他旧 packages 保留为归档源码，不承诺其 launcher/provider/webapp 旧契约能编译运行，也不为迁就历史代码重建 provider。
- 根目录旧 TypeScript build、db:generate、test:legacy、verify:legacy 及旧 runner/solution 已退役。共享 runtime preparer 移至 [scripts](<scripts/prepare-runtime.mjs>)，输出根 `runtime/`；Node 24.21.0 与 semver 7.8.5 由根显式锁定，npm 仍固定 12.0.2。CI/bootstrap 不再依赖已删 Shell。
- [共享依赖策略](<scripts/test/dependency-policy.test.mjs>)迁至当前测试目录，完整与 portable 入口均保留 authoritative lock/cache 与全部 advisory-range 检查，并增加仅当前 workspace 的安装图约束。Drizzle 专属 transform smoke test/override 随不再安装的 Drizzle 一同退役，不是跳过安全范围检查。
- 保留此前独立 DM 提取、EVA 捆绑与 pack helper 改动；本次源码清理没有安装/重载真实插件、修改 profile/凭据、启动/停止任何 Bot。下方旧验证数字是对应历史版本证据，不覆盖本次源码清理。
- 自带 Node/npm 离线重建根 lock 并 `ci --ignore-scripts --offline` 成功（6 个包）；没有全局安装或 profile 依赖安装。源码/manifest 检查 **25 模块**通过；最终定向 tooling **15/15 通过、0 skipped**，包含独立 EVA archive 离线 consumer、runtime 自删除拒绝与缺失 npm 不回退 PATH 的真实隔离进程测试。shell 语法与 diff whitespace 检查通过。未实际下载/重新准备 runtime，Windows 仅为 CI 合约，不能声称 Windows 实机通过。
- 父 Agent 最终独立复验：源码/resources **25 模块通过**，完整入口 **545/545 通过**、portable **115/115 通过**，均 **0 失败、0 skipped**；`git diff --check` 通过。真实原生 ClientModuleRegistry / ModuleLoader / composeEntries 只读探针 **12 项标记全部 true**；完整入口含真实 EVA archive 与离线 consumer 测试，另以 pack helper 实际生成 MAGI Shell archive 成功。portable 在 macOS 上执行，不替代 Windows 实机验收；没有声称 live 页面视觉验收。
- 与源码清理独立，主 Agent 报告一次性 `@local/magi-main-title-repair` 已经原生 `magiMainSession.current → sessions.get → sessionTitle.rename('主 Bot') → sessionPersistence.flush` 修复固定父会话 `session-899d4db2-b59b-491a-80b7-00473082fb15`，folded snapshot source 为 user。回执位于仓库外[原生重命名回执](<../magi-maintenance/main-title/receipt.json>)（2026-10-28T17:56:21.264Z）；维护 bundle 随后立即移除，无常驻插件、不重启 EVA/节点。此标题修复**不代表**独立 DM 源码提取已经部署。

### 最新：独立 `@magi/dm` 包，由 EVA 安装（源码完成，尚未应用）

- 用户确认 DM 应是 packages 内独立插件。新增 [@magi/dm 0.1.0](<packages/dm/README.md>)，抽走原 EVA 的 DM Host、工具、可靠投递/存储、`@user` 和 Client 投影；[EVA manifest](<apps/eva/package.json>)升级源码版本为 0.7.0，依赖并捆绑该包，[bundle patch](<apps/eva/cordis.patch.yml>)挂载独立根行。桌面继续经 EVA bundle 使用同一 DM，不增加第三份实现。DM 本身不是 bundle。
- EVA 只提供进程派生的 `magiDmTransport`，DM 通过 `magiDm` 发布安装实例。原固定主会话仍由 Shell/子启动器提供。DM 独立重载使用 effect-owned `bindBroker` 为已有进程重新挂接/握手，绝不偷偷重启 Bot。原始 hello 与主动 hello 并发安全；初始 hello 与 broker 重载竞态不会永久停掉 durable pump，且不提前启动、不晚于卸载启动、不制造模型唤醒。
- 子 overlay 版本 **3**：EVA child 与已安装 DM 的绝对根入口分开加载。总是插入独立 `magi-dm-child`；若已有 profile 选中 EVA，只对可能的原 bundle `magi-dm` 行作无 name 的禁用覆盖。原生 Loader 的 name 是断言，不能拿绝对路径重命名原 bare 名称。旧 bundle 无该行时仅 warning/skip，显式新 DM 根仍加载；新 bundle 的原行禁用后不触发同包重复 Client 来源。
- [真实原生只读探针](<apps/eva/test/client-modules-native.js>)使用官方 composeEntries / Context / ModuleLoader / ClientModuleRegistry 验证上项新旧 profile 情况，且两行同时活动的正控制确实报重复。startup hash 包含版本和解析后的 DM 入口；v1/v2 patch、profile manifest、身份、`magi-dm` 数据、wire/source ID、主/pair Session 均不迁移、不清理。
- 原生链接安装不会安装源码包依赖。本仓库已用 MAGI 自带 Node/npm 从锁文件执行 `ci --workspace @local/eva --ignore-scripts --offline --omit=dev`，只添加两个 workspace 链接，不修改 profile/全局工具配置。分发使用 [pack helper](<scripts/pack-dsh.mjs>)，以临时 staging 实体化捆绑依赖，避免普通 npm pack 漏掉 hoisted symlink。fixture 与实际 EVA archive 均在源码树外由 pnpm 离线安装，验证 DM Host/Client/locale/icon 导出可用。
- 工具 schema/描述和通知正文保持不变；逐字节对比基线确认 dm-host / rpc / store / tool / user 五个模块完全一致。最终 MAGI 自带 Node 检查 **25 模块/resources 通过**；完整入口 **540 项：539 通过、1 失败、0 skipped**，portable 入口 **110 项：109 通过、1 失败**。唯一失败均为既有[依赖策略检查](<scripts/test/dependency-policy.test.mjs>)缺少本地 `semver`（MODULE_NOT_FOUND）；不安装无关依赖、不删改/跳过完整入口中的检查，也不声称完整入口全绿。另显式排除这一已知环境检查的当前插件套件 **539/539 通过**，包含真实进程夹具、DM reload/admission 竞态、v1/v2 不可变 overlay、独立分发/离线安装与 Client 投影。原生元数据探针所有标记为 true。portable 仅在当前 macOS 上执行，不声称 Windows 实测。
- **未安装/重载运行插件，未 start/stop/wake 节点，未执行真实模型或新通知测试；没有修改凭据或模型配置。** 下方真实通知/页面验收属于重构前版本，不能复用为本次 live 证明。应用新包需用户新的明确安全窗口确认；父 EVA 重载会停止管理中的节点。旧 `test 12345` 通知不得为验收重复发送。

### 前次：同 Bot 跨 Session `@user` 通知（已应用，真实链路与页面验收通过）

- `dm.send` 的 `@user` / `user` 只向当前 Bot 已有、就绪的固定主会话发送，不走父 IPC、不建立 pair、不回退桌面主会话；调用源取可信 `exec.agent.id`，主会话内直接正常回复。保留地址冲突、固定主会话身份 / workspace / lineage、稳定 ID 与调用源、投递前所有者重新校验均失败关闭。
- 桌面与子 Host 启动所有者提供可选只读 `magiMainSession.current()`；仅初始化和持久化完成后发布，卸载立即不可用。主会话写入使用原生不唤醒 `Agent.inject`；[Chat 投影（现属独立 DM 包）](<packages/dm/client.js>)显示纯正文在 Agent 侧，不带 DM 头，拼接与后续消费只出现一条。两份预览 reader 同步只显示正文，不泄露内部来源说明。不会伪造原生助手消息。
- **边界**：`inject` 是 next-step 背景输入，主 Agent 后续步骤可能看到明确标记的非人类来源说明与引用正文；不是零模型上下文影响的日志侧信道，不传递人类授权，也不要求再次回复。已核查但不使用原生 `appendPluginRecord`：插件开发指南明确保留给 DSH experimental 包，不能将其当作 MAGI 的公开稳定通知接口。
- 私有 outbox 固定目标，原生 flush 后才 accepted；稳定重试、崩溃恢复和原生已 claim / consume / cancel 不重复注入。Bot 间原协议、父进程身份绑定、独立 pair 与不自动礼貌回声不变。
- 最终验证：25 源模块与 manifest 检查通过；完整入口 **518 项，517 通过、1 失败**，唯一失败仍是既有[依赖策略检查](<scripts/test/dependency-policy.test.mjs#L12>)缺少本地 `semver`。首次高并发全跑出现三个 fake-child 启动超时，独立重跑 21/21 通过，完整入口显式 `--test-concurrency=1` 后三项均通过；没有放宽产品超时或安装依赖。新增通知 Host 回归 41/41；Client/预览联合 47/47，通过原生 Session 验证 / JSON restore / 真 Conversation assembler 的精确正文测试；单入口相关回归 90/90，原生 Creator 初始声明与 30 模块导入探针也通过。离线证明与下述真实验收分开记录。
- DM 工具描述 + schema：2052 → 2398 字符（+346）；字符 / 4 粗估增加约 87 tokens，**未测实际首轮完整 prompt tokens**。
- 用户明确批准现在应用并测试后，通过 Plugin Manager 对 `include:eva` 禁用 / 启用完成重组；不卸载包、不安装依赖。父链接包改动与子端独立 eva 预设一起加载，既有 Session 没有迁移 preset。真实发送 `desktop-user-test-20261027-2025` 至 `eva-000`，子 Agent 在其独立 DM Session 自己调用工具发送 `eva-user-test-20261027-2025` 至 `@user`；子 outbox 的正文精确为 `test 12345`，目标为原固定主会话，状态 accepted、attempts 1；真实主会话预览也精确返回该正文。
- 部署核查发现原生 ClientModuleRegistry 会拒绝同包两个独立 Loader 来源。新增单一子端装配入口（历史 v2 包装器，本次已删除），合并 lifecycle + DM 注入依赖与 apply，升级 child Host overlay 为 2；保留旧不可变启动文件。[原生只读探针](<apps/eva/test/client-modules-native.js>)使用真实 Context / Registry 证实重复来源拒绝、单入口发现包 Client 与 Conversation / Chat 依赖。只替换 Loader 清单、不执行 Host 生命周期，不冒充 live DOM 证明。
- 再次安全应用后，核验真实已生成启动 patch 只含单一 `child-entry.js` 来源，`eva-000` 在线，固定 workspace UUID `a2a8bde2-134b-470b-9b85-958ea039b846` 与主会话 `session-955a2fee-7c8e-4a5e-9cc9-406ca72e2a7e` 均未变；测试通知重启后仍在，outbox 仍为原 accepted / attempts 1，没有为验收另发第二条相同正文。20:35 快照 PID 92752 / origin `http://127.0.0.1:58427` 仅供排查，不当作稳定配置。
- **页面验收**：2026-10-27 22:24 America/Edmonton，用户在当前 EVA-000 主会话明确确认“是，左侧显示正确且只有一条”，问题明确包含 Agent 左侧、正文仅 `test 12345`、无 `[MAGI-DM]` 前缀。视觉验收是用户确认；Agent 没有截图 / DOM 自动化能力，不伪称自行观测。实现、安全应用、真实模型转发、持久投递与实际页面验收均已完成，未提交或推送。

### 前次：桌面 / EVA Host 分工与独立 eva 预设（源码已实现，未重载）

- 桌面继续由 MAGI Shell 统一 Bot 导航；新增[子端专用装配](<apps/eva/child-host.js>)承接既有生命周期、DM、Codex 与凭据配置，不继承桌面 profile。新子 profile 仍由官方 CLI 的 web 模板初始化；已有子 profile 仅对已选择的 `@local/magi-shell` / `@local/eva` 管理入口添加禁用 overlay，不改 manifest、不卸载包、不剥离其他自定义插件，也不是任意插件安全过滤器。
- 当时新增独立 eva 声明（现已归入 [bot-mode 原生声明](<packages/bot-mode/presets/bot-preset.yml>)），不是 cordis 别名或动态继承。原生解析后与当前 Creator 的 23 个顶层插件配置深度一致，只将技能资源查找锚点改为 profileContext.installAnchor；保留全部工具、表达式和 planning / compaction / delegation 隔离。之后可独立维护该声明，不改桌面 Creator。
- [启动运行时](<apps/eva/runtime.js>)将原生 registry 部署默认设为 eva；用户已保存默认和会话显式选择仍优先。已有主会话、DM 会话、普通会话不迁移或强行换 preset，权限和审批调用不变。工具说明 1114 → 1169 字符，字符数 / 4 粗估增加约 14 tokens，未实测实际首轮完整 prompt tokenizer。
- 声明保存于子 home 的 `profiles/.eva-assets/<profile>/`，使用私有目录、内容 hash 和不可变写入保护；Host 版本、声明 hash、已知桌面角色排除列表进入新的启动 patch hash，旧文件保持原字节。**不能放在 profiles 树外**：预设子插件在那里没有原生 bare-import fallback；也不能预写实际 profile 内，因为首次 `--from-default-profile` 会整目录替换。Sibling cache 仅保证当前官方安装包解析，未承诺任意子 profile 私有包能从该缓存解析，也不承诺较早 profile patch 可以直接覆盖稍后 include 内的行。
- 新增[原生只读探针](<apps/eva/test/preset-native.js>)，使用 MAGI Electron Node-mode：原生 YAML 解码、Creator 初始快照对比、同类 synthetic profiles cache 锚点的全部 **30 个模块导入**及官方技能目录求值均通过，退出 0。探针不启动 Host、不写磁盘或 profile，不等于完整 Agent 激活、实际模型调用或 GUI 验收。
- 当前验证：MAGI 自带 Node v24.21.0，资源 / manifest / **23 个源码模块**检查通过；定向装配 / 生命周期 / Session 回归 **130/130** 通过；完整入口 **456 项，455 通过、1 失败**。唯一失败是既有[依赖策略检查](<scripts/test/dependency-policy.test.mjs#L12>)加载时缺少本地 `semver`，单独重跑确认 `MODULE_NOT_FOUND`，与此前相同；没有跳过此检查、安装依赖或声称完整入口全绿。
- 本次没有安装 / set_bundle / reload / start / wake / stop，也没有新建真实测试 Host。现有进程不会因源码变化而自动切换；需用户确认安全更新窗口后应用父 EVA 并正常重启子节点，再验证实际预设菜单和新会话。只刷新页面不会应用 Host 改动。

### 前次：整合远程清理后的历史与 CI 修复（2026-10-27）

- 已 fetch 并整合 origin/main 的 ff1c6686。本地原已提交 tree 与远程已清理的 ffa43d68 完全一致，不做会重新引入旧邮箱祖先的普通 merge；先保留私有完整 bundle 与 39 文件 binary patch，三方应用远程增量后将 main 对齐清理后的 origin/main，再恢复原本未暂存状态。原 39 文件补丁完整保留，除本文合并双方内容外逐文件补丁一致；无新增备份分支、提交或推送。本地私有备份与 reflog 仍保留回滚资料，不声称已清除本机历史对象。
- 仓库级 user.email 固定为 taki.wang@ualberta.ca，实际 Git author/committer identity 均已验证。当前 main 与 origin/main 相同，当前可达提交作者、提交者和提交消息均不含先前邮箱；工作树搜索也无匹配。没有修改全局 Git 身份。
- 保留远程 [仓库约束](<AGENTS.md>)、[源码检查](<scripts/check-dsh.mjs>)、[验收入口](<scripts/test.mjs>)、CI 与两个原子主会话初始化器的故障清理修复。[新增 import-closure 检查](<scripts/test/dsh-wiring.test.mjs>) 原正则会把 DM 数组中的 quoted from 字段误判为 import，已收紧并增加独立回归，不改 DM 协议迁就测试。
- MAGI 自带 Node 的源码/manifest 检查通过（22 模块）；完整入口执行 **430 项：429 通过，1 失败**。失败仅为 [共享依赖策略检查](<scripts/test/dependency-policy.test.mjs>) 加载本地缺失的 semver 时 MODULE_NOT_FOUND；没有跳过该检查、安装替代全局依赖或声称完整 npm test 全绿。425 项当前 EVA/Shell 回归与 4 项 wiring 回归通过。未安装或重载真实插件、未重启现有 EVA，DM 仍未上线。

### 前次：本地 Bot DM（已实现，未应用到运行中的 Host）

- 用户确认实现 DM；保留原有未提交改动，不创建提交。范围仅固定 Bot 对的显式 DM，不做群组或新 UI。新增 [DM 工具（现独立包）](<packages/dm/dm-tool.js>) 的 contacts/send/history/status/identity；profile 初次初始化固定 handle，显示名单独可改，子节点改名立即经重新 hello 刷新父/兄弟 contacts。
- [EVA 入口](<apps/eva/index.js>) 注册父 DM 域；[运行时](<apps/eva/runtime.js>) 把真实生成的 child IPC channel 与启动 handle 绑定到 broker，终止/失败/卸载时清理。启动 overlay 插入独立 子 DM 插件（历史入口已移除，现为[独立根](<packages/dm/index.js>)），hash 增加 dmProtocolVersion:1，保留以前的不可变 overlay。子角色标记和原主会话不变，不启动第二套 Agent loop 或网络端口，不用凭证授权 DM。注意绝对路径 overlay 入口不在 profile linkedRoots，不获 bare DSH import fallback；子入口必须通过 profileContext.installAnchor + createRequire.resolve 动态加载原生 defineTool。已用隔离官方 Electron Node 验证真实 native import 后到达 IPC guard；不启动 Host。
- [私有存储](<packages/dm/dm-store.js>) 在每端 profile 的 magi-dm 保存稳定身份、pair、inbox/outbox，私有权限、原子写/fsync、严格 JSON、拒绝 symlink/hardlink。每对 Bot 双方各有固定原生 Session；缺失/损坏或创建中断保留原保留 ID、失败关闭，不造替代 Session。[有界 IPC](<packages/dm/dm-rpc.js>) 仅 hello/contacts/deliver，48 KiB 包、32 并发、超时与断线清理，无远程 Session/工具控制。
- [本地域](<packages/dm/dm-host.js>) 仅显式 text 跨 Host，不自动转发模型、工具或 Session 结束输出。发送方用原生 inject 将显式发件正文放入其 DM Session 的 next-step 队列，保持后续回复上下文且不唤醒本方模型；发件前 flush，包括同进程失败重试时已有内存插入的分支。收件方 complete frozen UserMessage + followup，原生 inbox flush 成功后才写 accepted/回执。同一发送方等 ID 的并发发送与接收合并，异内容拒绝；入站存储、in-flight、恢复与 reply_to 使用 sender-scoped 哈希键，不同 peer 可用相同 wire ID；重试/回执不产生额外模型输入。待发送自动重试但不启动离线 Bot。
- 关键边界：accepted 是持久原生 inbox 接纳，不是模型执行/完成/回复。原生公开 API 没有安全的 wake-only；重启后只检查 inspect 的原始 splice/user-message 日志并标 attention，不激活 Agent、不使用已弃用 snapshotEvents、不取消重插、不合成控制消息或撤销用户停止。已 pending 等真正新输入自然唤醒；claimed 未提交可能是取消/失败，不盲目重放。attention 是当次恢复审计，可能落后后续执行。礼貌回声规则仅工具说明，不写人格/技能/重复提示；peer 输入不授予人类权限。
- 使用 MAGI 自带 Node 联合回归 **419/419 通过**；含 DM 域 23、RPC 20、存储 15、插件集成 10、子入口解析 2，以及启动 overlay / 进程 channel 生命周期 / 入口桥接。Session/Agent 为窄范围 API 夹具，进程启动使用 fake CLI；没有调用模型或声称真实跨 Host 端到端验收。完整约定与调用示例见 [EVA DM](<apps/eva/README.md#本地-bot-dm源码已实现待安全应用>)。
- **没有 set_bundle/reload/start/wake/stop 或新测试 Host。** 再查现有 eva-000 仍 running、PID 72612，workspace UUID 与固定主 Session ID 保持不变。源码尚未应用到父 Host/旧子节点，需用户确认安全更新窗口；重新应用父 EVA 可能中断托管节点，不能把离线测试等同已上线能力。

### 历史：修正「新 Bot」与顶部侧栏折叠按钮

- 用户对比原生截图指出按钮丑，并确认「那你做掉」。修改仅限 MAGI Shell Client：新 Bot 恢复横向铺满、圆角主题底色、细边框、会话加号图标与本地化文字；不再只是裸文字。折叠/展开改用侧栏分栏图标，置于自有顶部 chrome 行，移除品牌旁的小尖括号。macOS 的 52px 顶部带承载按钮，不再只给品牌行加顶部空白；折叠栏/隐藏侧栏使用同一套可访问图标按钮，原生 toggleSidebar 行为不变。
- 不更改新 Bot 的 POST、去重、固定 handle 和异步导航竞态逻辑；不修改官方 Harness 源码，不引入原生内部组件依赖，不触碰 EVA 保活实现。新增两项声明/事件回归；MAGI 自带 Node 执行 Shell **77/77**、EVA + Shell **345/345** 通过，Client 语法与差异检查退出 0。不是浏览器或像素测试。
- 仅重新应用 `@local/magi-shell`，结果 `application:applied`、warnings 为空；当前 GUI live Slot 中 `sidebar.brand.name` 的 magi-shell 已 active。应用使 MAGI 模式回到关闭状态，`shell.leading` 为原生 occupant；需要点击品牌旁切换按钮重新进入 MAGI。没有运行/发现 dev:web watcher，不承诺自动更新；如原页面仍旧请刷新现有 URL `http://127.0.0.1:58705`。没有浏览器操作/截图工具，实际明暗主题外观与点击验收待用户页面确认。
- 没有重新应用 EVA 或 start/wake/stop；实查 `eva-000` 仍 running、PID 72612、origin `http://127.0.0.1:50626`，原 workspace UUID、固定主会话和预览保持不变。

### 取消 EVA 页面数量限制并补齐恢复与观测

- 用户质疑三页上限及未经测量的 iframe 内存判断；确认「好，你做吧」后删除硬编码上限、LRU 与访问淘汰逻辑。首次打开才创建，当前父页内保留所有访问过的原生 EVA 页面；继续独立 Host，不预先重写跨 Host 单 UI。关闭/刷新父页、卸载插件、节点实际停止/移除/代次或身份变化、明确重试才清理/重建对应页面；普通切换和消息预览不重新认证。
- 另外修复原先超时/iframe 错误会通过父级条件渲染销毁 iframe 的漏洞：改为覆盖现有页面，允许同一窗口的可信就绪握手恢复，明确重试才重新 POST open。清单短暂网络失败保持缓存；失败与恢复不混同于首次成功。
- 可选只读 Client Inspect Provider `EVAFrameTiming.snapshot({})` 返回保留数、`pageLimit:null` 和至多 50 条脱敏页面摘要。父浏览器单调时钟记录首次请求准备、首次选择到真实子页 ready，以及自有 anchor 到下一 rAF 的调度时间（不是点击/像素绘制耗时）。没有逐次访问历史或任何计时驱动的页面淘汰；不包含 URL/凭据/Session ID/工作区/消息。只读查询不执行导航、探测或内存采样，失败和后续恢复独立记录，重复握手不重写原结果。Registry 服务从已安装 Client runner 源码核实，使用可选 Cordis 注入，不阻塞原生子页启动。
- 父 Agent 使用 MAGI 自带 Node 独立运行 EVA + MAGI Shell 联合回归 **343/343 通过**，Client 语法和 `git diff --check` 退出 0。新增八页往返及暂时清单故障、脱敏计时与失败分类、错误/超时同 iframe 恢复三项回归；原三页测试改为全保留与实际移除取消。独立复核发现 Host 的 launchUrl 在同一代次内可能恒定，显式重试不能只用 src 作为 React key；已添加 preparedAttempt 门控与 epoch key，防止批处理保留旧 ready 状态或准备中挂载旧地址，回归改用相同 URL 并断言新代次。最终复核无剩余范围内问题，父 Agent 再跑全套仍 343/343。测试是窄范围 VM 声明/生命周期夹具，不是浏览器 DOM/视觉、真实 React 批处理或性能数字。
- 无需重新应用 Host：当前 GUI `http://127.0.0.1:58705` 已发现新 `EVAFrameTiming`，真实查询返回 `retainedCount:0`、`pageLimit:null`，证明连接的页面运行新 Client；没有重载 Host 或调用 start/wake，`eva-000` 仍 PID 72612、origin `http://127.0.0.1:50626`，原 workspace UUID、固定主会话、消息预览保持不变。未验证 dev:web watcher，不承诺后续改动自动刷新。
- 实测边界：新 Client 尚无页面打开样本，因此首次打开/返回耗时**尚未测得**。真实查询内存能力为 secure context=true、crossOriginIsolated=false、应用内存 API=false；内存状态 not-measured、perFrameBytes=null，没有 MB 数字。父页 heap/Host RSS 不能代表每个跨 origin iframe 内存；不修改隔离头、不启动替代浏览器、不读取个人 profile/凭据。不设猜测上限。

### 历史：EVA 面板重复打开的首次修复（其中三页限制已被上节取消）

- 用户反馈每次点开 EVA 都加载太久。代码证据：原生 `MainPanel` 只渲染当前 `main` key；原 EVA 面板卸载会丢失本地 src/ready 状态并销毁 iframe。返回时重新 POST `/api/eva/open`、嵌入登录、加载子页面、打开固定主会话。这与节点是否已经自动运行是两个问题，不是再次启动节点或重复创建主会话。
- 修复在插件内保留最近使用的页面，继续使用独立子 Host 原生聊天，不另写聊天界面或更改 Session/凭据边界。`main` 只提供自有定位占位，常驻 `shell.overlay` 持有 iframe；只测量插件自有 ref、按矩形定位，不移动 iframe DOM，不替换 root/Conversation、不修改官方源码。保活限 3 个，首次选择才加载；释放旧代次、离线/移除和 LRU 淘汰的页面。原有 main 地址、唤醒和错误重试继续兼容。
- 清单新增可选 `instanceId`：仅对运行中、PID 与 origin 有效的进程发布基于 handle/PID/origin 的 24 位十六进制摘要。普通预览变化不改变代次；两次轮询间停启但 Session ID 未变时也能清理旧页面。API 仍不返回原始 PID/origin/登录地址，不请求 `launchUrl()`。此项不修改 Agent 工具 schema 或输出。
- 验证：父 Agent 独立运行 EVA + MAGI Shell 联合回归 **340/340 通过**，语法检查与 `git diff --check` 均退出 0。新增 4 项 Client 保活和 2 项 Host 实例代次测试，覆盖 A→主机器人→A、A→B→A 的缓存 key/POST 次数、预览更新不重注册、3 页 LRU、离线/移除/代次失效、过期认证结果、只在活动页计时及卸载清理。
- 用户选择「现在一起应用」后，通过 `set_bundle @local/eva enabled:true` 应用（`application: applied`、无 warnings），没有调用显式 start/wake。`eva-000` 已自动恢复为 running，新 PID 72612、origin `http://127.0.0.1:50626`；workspace UUID `a2a8bde2-134b-470b-9b85-958ea039b846` 和固定主会话 `session-955a2fee-7c8e-4a5e-9cc9-406ca72e2a7e` 与更新前相同，原消息预览仍可读。没有创建其他 Bot 或替代会话。
- 应用后再次查询当前 GUI `http://127.0.0.1:58705` 的 live Slot，确认 `shell.overlay` 中 `eva:retained-frames` 注册且 active；未承诺 dev:web watcher 或自动构建。实例代次字段的行为由 API 单元测试验证，没有借用或提取浏览器凭据做实时 API 请求。
- 验证范围：上述测试仅验证常驻注册、缓存声明身份与资源清理，没有浏览器操作/截图工具，不把 VM hook fixtures 当作真实 iframe DOM、切换耗时或视觉验收；live Slot 注册也不等于实际页面点击验证。

### EVA 插件启动自动恢复已有节点

- 用户要求插件启动时自动拉起已配置 EVA。新增 `apps/eva/autostart.js`，在父 Host 原生 `appReady.onReady()` 后非阻塞执行一次恢复；这里「配置」沿用现有安全持久化节点清单，不添加另一份配置文件或修改 profile。Host 入口新增 `appReady` 注入、一次性 guard、生命周期 AbortController，分发清单包含新增模块。
- 只恢复现有清单中的 `not-started` 固定 handle，跳过启动中/运行中/停止中及本轮已经 stopped/failed 的节点。校验 handle、去重、最多两个并发，单个失败隔离且日志不暴露原始异常；清单为空不分配新 Bot，无后台重启循环。不修改原 `eva` 工具 schema、描述及单次审批，也不修改浏览器新建/唤醒鉴权；子 Host 仍完全跳过启动器。
- 卸载时取消就绪监听并中止恢复，再由原 `manager.dispose()` 统一处理启动中的/运行中的子进程。复用原 owner lock、凭据共享、固定主会话与数据根；不接管外部锁、不删除数据、不根据磁盘 PID 杀进程。
- 最终联合回归 **334/334 通过**，新增 13 项自动恢复测试，覆盖真实安全清单、空清单不分配、无效身份过滤、并发与故障隔离、取消/卸载、就绪重复回调以及工具 start/stop 仍须 allowed-once。使用 MAGI 自带 Node；入口/新增 helper 语法检查与 `git diff --check` 通过。父 Agent 独立执行了最终 334 项全套及差异检查，之前也独立完成入口语法检查；应用后最后新增仅为工具审批测试，运行时代码未再改变。
- 当前已通过 `@local/eva set_bundle enabled:true` 应用，`application:applied`、warnings 为空。未调用任何显式 start/wake，随后 `eva status` 实测 `eva-000` 自动成为 running（PID 71274），workspace UUID 仍为 `a2a8bde2-134b-470b-9b85-958ea039b846`，主会话仍为 `session-955a2fee-7c8e-4a5e-9cc9-406ca72e2a7e`，真实消息预览可读取。没有分配额外节点或发送模型请求；这验证自动恢复，不代表浏览器视觉验收或账号模型调用验收。

### 精简导航与真正的新 Bot

- MAGI 模式不再显示「插件」「自动化任务」「更多」，将「新会话」替换为「新 Bot」。原生新会话按钮没有独立公开替换席位，因此仅在 MAGI 打开期间以条件 `sidebar` 与 `shell.leading` occupant 提供精简导航；关闭 MAGI 恢复原生侧栏。下文 `sidebar.workspaces`-only 的布局说明属于此前版本，不代表当前实现。原生 root/main、聊天、历史与输入均不替换，不修改官方源码，不靠 DOM 文本选择器隐藏入口；设置等入口仍可切回 DSH 使用。
- 「新 Bot」通过已认证 transport 显式 POST `/api/eva/create`，复用 EVA 工具的 `manager.start(undefined)` 自动分配独立节点。后端只接纳原生 `browserPeer`，API owner/Agent 凭据不能绕过工具审批；沿用无凭据桌面 IPC 安全拒绝行为。请求取消不取消管理器的生命周期归属，插件卸载仍负责停止子进程。
- 前后端均合并创建中的重复请求，错误显式重试且不自动重发 POST。创建后刷新清单并选择现有 EVA panel；panel 注册竞态只重试选择同一 Bot，不另建节点。异步结果不会覆盖用户较新的导航，退出/卸载抑制迟到更新。中英文标签、折叠栏及 macOS 完全隐藏侧栏的 leading 控件均有覆盖。头像保持固定配色圆角块与独立状态点。
- 本次联合回归 **321/321 通过**，使用 MAGI 自带 Node；父 Agent 又独立运行了全套测试、两个变更入口的语法检查及 `git diff --check`，均通过。测试使用假管理器，不创建真实节点，不声称浏览器点击或像素验收。
- 用户明确允许重载并停止正在运行的 `eva-000` 后，`@local/eva enabled:true` 协调更新，MAGI Shell 顺序关闭/启用，三次均 `application:applied`、warnings 为空。`eva status` 确认为 not-started；固定主会话映射仍为 `session-955a2fee-7c8e-4a5e-9cc9-406ca72e2a7e`，未新建或替换。没有擅自重新启动节点。
- 当前页面 `http://127.0.0.1:58705` 的 `sidebar.brand.name` live Inspect 确认 `magi-shell` 重新注册并 active；重载后默认回到 DSH，需要点击切换进入 MAGI 才挂载条件导航。无可用浏览器操作/截图工具，未执行真实「新 Bot」点击、折叠/展开、明暗主题视觉验收。

### Bot 列表消息预览与当前源码链接

- 当前 profile 的 `@local/eva` 与 `@local/magi-shell` 已通过 Plugin Manager 改为链接 `/Users/shichuan/.dsh/workspace/MAGI/apps/eva`、`/Users/shichuan/.dsh/workspace/MAGI/apps/magi-shell`；后文旧工作区路径仅为历史记录。
- Bot 条目的第二行改为最近用户/助手消息预览；状态仅保留圆点，不再显示「在线/离线 · 主会话」。空会话显示「暂无消息」，预览不可读取与空会话分开处理。
- 主机器人通过原生 Session Query 读取自己的固定会话；子 Host 读取自己的固定主会话并通过已有私有进程 IPC 上报有长度限制的预览。清单只公开允许的字段，不读取其他 profile 的会话文件，不转发工具输出、系统注入或思考内容，不暴露登录地址。
- 本次组合回归测试 **310/310 通过**（MAGI 自带 Node），`git diff --check` 通过。两个 bundle 再次 `set_bundle enabled:true` 均返回 `application:applied`、无 warnings；为确保 Client bundle 重新读取，另对 `@local/magi-shell` 顺序关闭/启用，均已 applied。重载前后均无运行中的 EVA；运行中 `eva status` 已返回新的 `preview.state:unavailable` 字段，现有 GUI 的 `sidebar.brand.name` 检查确认 `magi-shell` 已重新注册。Shell 重载会回到原生浏览模式，需再次点击切换进入 MAGI。未获取真实截图或操作浏览器刷新，因此不声称视觉验收完成。
- 源码已链接不代表正在运行的 Host/Client 必然已更新。对已启用 bundle 再次调用 `set_bundle enabled:true` 也会协调链接源码变化；以返回的 `application`、warnings 和真实能力为准，不能只看 `changed`。EVA 重载可能停止其节点，操作前确认状态。离线发现的节点无消息缓存时显示预览不可用；已管理节点仅保留内存中的最后预览，不跨 profile 读取历史。预览显示已提交的消息而非 token 流，子节点更新最多需两个轮询周期。

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

## 当前 CI / 验收入口（2026-10-27）

- 当前生产入口是 `@local/eva`、`@local/magi-shell` 及独立 `@magi/dm` 的 Host / Client exports。EVA 的 bundle 显式挂载 DM；仅依赖这些包、Node 和官方 DSH 服务，不调用旧 Shell 或其他 legacy packages。
- 根 `npm test` 现直接检查两个插件的源模块语法、分发资源与锁元数据，运行全部当前插件回归和依赖安全策略。Linux/macOS 执行完整套件；Windows 明确仅执行 portable Client/认证/导航/包契约，不冒称 POSIX Host 生命周期已通过。
- CI 不再编译旧 Electron UI、运行旧 EVA self-rebuild IPC 或构建/上传 AppImage、安装器；手动运行也没有打包步骤。保留旧源码与断言于显式 `test:legacy`，不是删掉有意义的断言来凑绿，也不是声称旧 Shell 已迁移。
- 当前 DSH 原子身份持久化新增对两个真实 public initializer 的 partial-write / rename 故障回归；写失败清理仅限本次私有临时文件，原 mapping 与 Session identity 保持。真实子进程归属、并发启动、readiness、auth/凭据与持久化断言继续由当前插件测试覆盖。
- 详见 [测试范围与平台边界](docs/testing.md)。这些 CI 不安装/重载真实 DSH 插件、不触发模型服务，也不替代原生 GUI / live 模型验收。

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
runtime/bin/node --test apps/eva/test/*.test.js
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
- 三项旧 Shell 测试已直接从仍保留的 `@magi/launcher/composition/agent.js` 导入 `Magi`，绕过被删除的同名 EVA 转导出文件；不改变旧 composition 的行为或宣称它已改造成 DSH。旧 `atomic-launcher-state` 与 launcher 启动／集成测试作为历史断言保留在 `test:legacy`；当前 DSH 验收改为两个真实插件的入口，原子状态与进程安全契约有对应当前回归。不能据此声称旧 launcher 已迁移或 legacy suite 已通过。
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

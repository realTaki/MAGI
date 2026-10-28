# MAGI → DSH 重构交接

## 当前补充：MAGI Shell

### 最新：同 Bot 跨 Session `@user` 通知（已应用，真实链路与页面验收通过）

- `dm.send` 的 `@user` / `user` 只向当前 Bot 已有、就绪的固定主会话发送，不走父 IPC、不建立 pair、不回退桌面主会话；调用源取可信 `exec.agent.id`，主会话内直接正常回复。保留地址冲突、固定主会话身份 / workspace / lineage、稳定 ID 与调用源、投递前所有者重新校验均失败关闭。
- 桌面与子 Host 启动所有者提供可选只读 `magiMainSession.current()`；仅初始化和持久化完成后发布，卸载立即不可用。主会话写入使用原生不唤醒 `Agent.inject`；[Chat 投影](<apps/eva/client.js>)显示纯正文在 Agent 侧，不带 DM 头，拼接与后续消费只出现一条。两份预览 reader 同步只显示正文，不泄露内部来源说明。不会伪造原生助手消息。
- **边界**：`inject` 是 next-step 背景输入，主 Agent 后续步骤可能看到明确标记的非人类来源说明与引用正文；不是零模型上下文影响的日志侧信道，不传递人类授权，也不要求再次回复。已核查但不使用原生 `appendPluginRecord`：插件开发指南明确保留给 DSH experimental 包，不能将其当作 MAGI 的公开稳定通知接口。
- 私有 outbox 固定目标，原生 flush 后才 accepted；稳定重试、崩溃恢复和原生已 claim / consume / cancel 不重复注入。Bot 间原协议、父进程身份绑定、独立 pair 与不自动礼貌回声不变。
- 最终验证：25 源模块与 manifest 检查通过；完整入口 **518 项，517 通过、1 失败**，唯一失败仍是既有[依赖策略检查](<apps/shell/test/dependency-policy.test.mjs#L12>)缺少本地 `semver`。首次高并发全跑出现三个 fake-child 启动超时，独立重跑 21/21 通过，完整入口显式 `--test-concurrency=1` 后三项均通过；没有放宽产品超时或安装依赖。新增通知 Host 回归 41/41；Client/预览联合 47/47，通过原生 Session 验证 / JSON restore / 真 Conversation assembler 的精确正文测试；单入口相关回归 90/90，原生 Creator 初始声明与 30 模块导入探针也通过。离线证明与下述真实验收分开记录。
- DM 工具描述 + schema：2052 → 2398 字符（+346）；字符 / 4 粗估增加约 87 tokens，**未测实际首轮完整 prompt tokens**。
- 用户明确批准现在应用并测试后，通过 Plugin Manager 对 `include:eva` 禁用 / 启用完成重组；不卸载包、不安装依赖。父链接包改动与子端独立 eva 预设一起加载，既有 Session 没有迁移 preset。真实发送 `desktop-user-test-20261027-2025` 至 `eva-000`，子 Agent 在其独立 DM Session 自己调用工具发送 `eva-user-test-20261027-2025` 至 `@user`；子 outbox 的正文精确为 `test 12345`，目标为原固定主会话，状态 accepted、attempts 1；真实主会话预览也精确返回该正文。
- 部署核查发现原生 ClientModuleRegistry 会拒绝同包两个独立 Loader 来源。新增[单一子端装配入口](<apps/eva/child-entry.js>)，合并 lifecycle + DM 注入依赖与 apply，升级 child Host overlay 为 2；保留旧不可变启动文件。[原生只读探针](<apps/eva/test/client-modules-native.js>)使用真实 Context / Registry 证实重复来源拒绝、单入口发现包 Client 与 Conversation / Chat 依赖。只替换 Loader 清单、不执行 Host 生命周期，不冒充 live DOM 证明。
- 再次安全应用后，核验真实已生成启动 patch 只含单一 `child-entry.js` 来源，`eva-000` 在线，固定 workspace UUID `a2a8bde2-134b-470b-9b85-958ea039b846` 与主会话 `session-955a2fee-7c8e-4a5e-9cc9-406ca72e2a7e` 均未变；测试通知重启后仍在，outbox 仍为原 accepted / attempts 1，没有为验收另发第二条相同正文。20:35 快照 PID 92752 / origin `http://127.0.0.1:58427` 仅供排查，不当作稳定配置。
- **页面验收**：2026-10-27 22:24 America/Edmonton，用户在当前 EVA-000 主会话明确确认“是，左侧显示正确且只有一条”，问题明确包含 Agent 左侧、正文仅 `test 12345`、无 `[MAGI-DM]` 前缀。视觉验收是用户确认；Agent 没有截图 / DOM 自动化能力，不伪称自行观测。实现、安全应用、真实模型转发、持久投递与实际页面验收均已完成，未提交或推送。

### 前次：桌面 / EVA Host 分工与独立 eva 预设（源码已实现，未重载）

- 桌面继续由 MAGI Shell 统一 Bot 导航；新增[子端专用装配](<apps/eva/child-host.js>)承接既有生命周期、DM、Codex 与凭据配置，不继承桌面 profile。新子 profile 仍由官方 CLI 的 web 模板初始化；已有子 profile 仅对已选择的 `@local/magi-shell` / `@local/eva` 管理入口添加禁用 overlay，不改 manifest、不卸载包、不剥离其他自定义插件，也不是任意插件安全过滤器。
- 新增独立 [eva 声明](<apps/eva/eva-preset.yml>)，不是 cordis 别名或动态继承。原生解析后与当前 Creator 的 23 个顶层插件配置深度一致，只将技能资源查找锚点改为 profileContext.installAnchor；保留全部工具、表达式和 planning / compaction / delegation 隔离。之后可独立维护该声明，不改桌面 Creator。
- [启动运行时](<apps/eva/runtime.js>)将原生 registry 部署默认设为 eva；用户已保存默认和会话显式选择仍优先。已有主会话、DM 会话、普通会话不迁移或强行换 preset，权限和审批调用不变。工具说明 1114 → 1169 字符，字符数 / 4 粗估增加约 14 tokens，未实测实际首轮完整 prompt tokenizer。
- 声明保存于子 home 的 `profiles/.eva-assets/<profile>/`，使用私有目录、内容 hash 和不可变写入保护；Host 版本、声明 hash、已知桌面角色排除列表进入新的启动 patch hash，旧文件保持原字节。**不能放在 profiles 树外**：预设子插件在那里没有原生 bare-import fallback；也不能预写实际 profile 内，因为首次 `--from-default-profile` 会整目录替换。Sibling cache 仅保证当前官方安装包解析，未承诺任意子 profile 私有包能从该缓存解析，也不承诺较早 profile patch 可以直接覆盖稍后 include 内的行。
- 新增[原生只读探针](<apps/eva/test/preset-native.js>)，使用 MAGI Electron Node-mode：原生 YAML 解码、Creator 初始快照对比、同类 synthetic profiles cache 锚点的全部 **30 个模块导入**及官方技能目录求值均通过，退出 0。探针不启动 Host、不写磁盘或 profile，不等于完整 Agent 激活、实际模型调用或 GUI 验收。
- 当前验证：MAGI 自带 Node v24.21.0，资源 / manifest / **23 个源码模块**检查通过；定向装配 / 生命周期 / Session 回归 **130/130** 通过；完整入口 **456 项，455 通过、1 失败**。唯一失败是既有[依赖策略检查](<apps/shell/test/dependency-policy.test.mjs#L12>)加载时缺少本地 `semver`，单独重跑确认 `MODULE_NOT_FOUND`，与此前相同；没有跳过此检查、安装依赖或声称完整入口全绿。
- 本次没有安装 / set_bundle / reload / start / wake / stop，也没有新建真实测试 Host。现有进程不会因源码变化而自动切换；需用户确认安全更新窗口后应用父 EVA 并正常重启子节点，再验证实际预设菜单和新会话。只刷新页面不会应用 Host 改动。

### 前次：整合远程清理后的历史与 CI 修复（2026-10-27）

- 已 fetch 并整合 origin/main 的 ff1c6686。本地原已提交 tree 与远程已清理的 ffa43d68 完全一致，不做会重新引入旧邮箱祖先的普通 merge；先保留私有完整 bundle 与 39 文件 binary patch，三方应用远程增量后将 main 对齐清理后的 origin/main，再恢复原本未暂存状态。原 39 文件补丁完整保留，除本文合并双方内容外逐文件补丁一致；无新增备份分支、提交或推送。本地私有备份与 reflog 仍保留回滚资料，不声称已清除本机历史对象。
- 仓库级 user.email 固定为 taki.wang@ualberta.ca，实际 Git author/committer identity 均已验证。当前 main 与 origin/main 相同，当前可达提交作者、提交者和提交消息均不含先前邮箱；工作树搜索也无匹配。没有修改全局 Git 身份。
- 保留远程 [仓库约束](<AGENTS.md>)、[源码检查](<scripts/check-dsh.mjs>)、[验收入口](<scripts/test.mjs>)、CI 与两个原子主会话初始化器的故障清理修复。[新增 import-closure 检查](<scripts/test/dsh-wiring.test.mjs>) 原正则会把 DM 数组中的 quoted from 字段误判为 import，已收紧并增加独立回归，不改 DM 协议迁就测试。
- MAGI 自带 Node 的源码/manifest 检查通过（22 模块）；完整入口执行 **430 项：429 通过，1 失败**。失败仅为 [共享依赖策略检查](<apps/shell/test/dependency-policy.test.mjs>) 加载本地缺失的 semver 时 MODULE_NOT_FOUND；没有跳过该检查、安装替代全局依赖或声称完整 npm test 全绿。425 项当前 EVA/Shell 回归与 4 项 wiring 回归通过。未安装或重载真实插件、未重启现有 EVA，DM 仍未上线。

### 前次：本地 Bot DM（已实现，未应用到运行中的 Host）

- 用户确认实现 DM；保留原有未提交改动，不创建提交。范围仅固定 Bot 对的显式 DM，不做群组或新 UI。新增 [DM 工具](<apps/eva/dm-tool.js>) 的 contacts/send/history/status/identity；profile 初次初始化固定 handle，显示名单独可改，子节点改名立即经重新 hello 刷新父/兄弟 contacts。
- [EVA 入口](<apps/eva/index.js>) 注册父 DM 域；[运行时](<apps/eva/runtime.js>) 把真实生成的 child IPC channel 与启动 handle 绑定到 broker，终止/失败/卸载时清理。启动 overlay 插入独立 [子 DM 插件](<apps/eva/dm-child.js>)，hash 增加 dmProtocolVersion:1，保留以前的不可变 overlay。子角色标记和原主会话不变，不启动第二套 Agent loop 或网络端口，不用凭证授权 DM。注意绝对路径 overlay 入口不在 profile linkedRoots，不获 bare DSH import fallback；子入口必须通过 profileContext.installAnchor + createRequire.resolve 动态加载原生 defineTool。已用隔离官方 Electron Node 验证真实 native import 后到达 IPC guard；不启动 Host。
- [私有存储](<apps/eva/dm-store.js>) 在每端 profile 的 magi-dm 保存稳定身份、pair、inbox/outbox，私有权限、原子写/fsync、严格 JSON、拒绝 symlink/hardlink。每对 Bot 双方各有固定原生 Session；缺失/损坏或创建中断保留原保留 ID、失败关闭，不造替代 Session。[有界 IPC](<apps/eva/dm-rpc.js>) 仅 hello/contacts/deliver，48 KiB 包、32 并发、超时与断线清理，无远程 Session/工具控制。
- [本地域](<apps/eva/dm-host.js>) 仅显式 text 跨 Host，不自动转发模型、工具或 Session 结束输出。发送方用原生 inject 将显式发件正文放入其 DM Session 的 next-step 队列，保持后续回复上下文且不唤醒本方模型；发件前 flush，包括同进程失败重试时已有内存插入的分支。收件方 complete frozen UserMessage + followup，原生 inbox flush 成功后才写 accepted/回执。同一发送方等 ID 的并发发送与接收合并，异内容拒绝；入站存储、in-flight、恢复与 reply_to 使用 sender-scoped 哈希键，不同 peer 可用相同 wire ID；重试/回执不产生额外模型输入。待发送自动重试但不启动离线 Bot。
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

- 当前生产入口是 `@local/eva` 与 `@local/magi-shell` 的 Host / Client exports 及各自 `cordis.patch.yml`。逐模块核验仅依赖同 bundle、Node 和官方 DSH 服务；不再调用旧 `apps/shell` 或 `packages/*`。
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

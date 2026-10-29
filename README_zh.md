# MAGI — Modular Agentic Genesis Intelligences

[![License](https://img.shields.io/badge/license-BUSL--1.1-blue.svg)](LICENSE)
[![TypeScript](https://img.shields.io/badge/runtime-TypeScript%20%2B%20Node%2024-blue)](https://nodejs.org/)
[![Status](https://img.shields.io/badge/status-experimental-orange)](docs/verification.md)

[English](README.md) · [开始使用](docs/running.md) · [架构](docs/architecture.md) · [文档导航](docs/README.md)

> **正在迁移至 DSH。** `apps/eva` 现为 DSH Host 插件，不再是原来的 EVA launcher。本文及关联文档中的桌面／Shell 与修订切换流程属于迁移前实现，尚未完成与新插件的集成。操作前请先阅读[当前范围与验收边界](DSH-HANDOFF.md)。

当前 DSH 功能分为两个产品包与共享组件：**MAGI 工作台**负责桌面导航，附带 **Bot 主会话 / Bot 能力预设**（[@magi/bot-mode](<packages/bot-mode/README.md>)）；**EVA 节点管理**负责独立节点生命周期，附带 **Bot 私信**（[@magi/dm](<packages/dm/README.md>)）和 **Bot 记忆**（[@magi/memory](<packages/memory/README.md>)），子节点复用 Bot mode。实现保持独立，包名与数据归属不变。安装包卡片不等于运行实例；EVA 与独立记忆包不必重复选择，具体见[插件分层与更新边界](<docs/plugin-layout.md>)。

分发插件使用[专用打包脚本](<scripts/pack-dsh.mjs>)，由 MAGI 自带 Node 执行 `scripts/pack-dsh.mjs --app eva --out dist`（也支持 `magi-shell`、`memory`、`bot-mode`）。旧 Electron Shell 和 provider 模块已删除。当前安装图仅包含上述五个 workspace；其他迁移前 packages 仅为不参与安装的归档源码，根目录不再提供 legacy 构建／验收入口。下方历史架构与修订切换说明不是当前启动指南。

**Fork 一份，养出你自己的 MAGI。**

MAGI 探索的是一种新的软件形态：Agent 不只是替你使用软件，也能修改软件，让它逐渐长成你需要的样子。

它从一个持续存在的多智能体系统出发。源码不是安装完成后就封存的东西，而是 Agent 工作环境的一部分。
Agent 可以修改源码、检验结果，并请求切换到新的运行修订。

我想做的，不是一个所有人都用得一模一样的成品助手，而是一份可以继续生长的软件起点。
**你的 MAGI，本来就应该和我的不一样。**

名字致敬《新世纪福音战士》。

## 不只是配置软件，而是把它养成你的软件

大多数设置，是从作者预先实现的行为里做选择。MAGI 想探索另一种关系：
你说出需求，让 Agent 直接参与改造软件本身。

可以是改一个工具、做一个只适合你的界面、调整一套工作流程，也可以参考其他 Agent 项目的实现，
把有用的能力带进来。每一次改造，都从具体需求出发，用实际结果判断值不值得留下。

我不打算把它做成一个商业化的、所有用户保持一致的托管产品，也不承诺向后兼容。
我默认你有自己的 Agent，或者 MAGI 本身就是那个 Agent：你们一起维护自己的 fork，选择要吸收的变化，处理升级和迁移。
上游提供想法和代码，不规定你的版本最终必须是什么样子。

## 它靠什么支撑这种可能？

### Agent team 不必随任务结束而消失

**EVA** 是一个独立的 Agent 节点，拥有自己的进程、工作区、模型提供方配置、记忆和联系人。
桌面应用启动内置的 **Shell EVA**，作为你进入这个系统的入口。你可以在聊天中让 Shell 创建新的本地 EVA，或者连接远程 EVA。

节点之间直接交换消息，各自保存历史，不依赖中心化 ASP 注册表或中继。
一个团队不只是临时拼起来的几次模型调用：节点和工作区可以在一次任务结束之后继续存在。

### 用 BUS 连接能力，而不是把实现绑在一起

每个 EVA 内部的 worker 通过 **BUS 的 Books 和持久化 Jobs** 协作，而不是直接调用其他 worker 的实现。
BUS 承载共享数据与契约，worker 提供具体能力，manager 管理 worker 的生命周期和配置。

这样拆开，是为了让 Agent 改造某个能力时，不必总是牵动整套系统。
稳定的协作边界，让具体模块可以继续变化。具体结构见[架构文档](docs/architecture.md)。

### 界面也不是不可改变的外壳

“Everything is an agent” 是方向：不只有聊天里的助手，你的界面、工具和自定义系统，也应该逐渐具备参与自身维护的能力。

当前 MAGI 已有可编辑源码和经过验证门控的运行修订切换机制，Shell 修订也可以包含构建后的 UI。
下一步，是让系统更多地自己管理自己，逐渐减少对手工配置的依赖。
我希望有一天，连 settings 页面都不再需要。

## 从你自己的版本开始

1. **先 fork 这个仓库。** 给 MAGI 为你做出的修改一个自己的家。不是每一项个人定制都需要提交给上游。
2. **运行准备好的源码检出或合适的桌面构建。** 具体步骤见[运行指南](docs/running.md)，源码准备和自带 Node 24 工具链见[开发指南](CONTRIBUTING.md)。
3. **为要使用的 EVA 配置模型。** 在对应本地 EVA 的 profile 中填写，不要把密钥贴进聊天。等待该节点的验证结果。
4. **从一个小而有用的改动开始。** 说清范围和预期，检查结果，留下证据。保存源码、测试、激活和发布，是不同的动作。

第一次自修改，可以选一个看得见、测得出的界面行为。构建通过之后，还要实际检查行为有没有按预期改变。

## 当前边界

MAGI 仍是实验项目。愿景可以走得远，当前能力要说清楚：

- **当前只面向可信私有网络。** 对等消息协议不是经过身份认证的公网服务，不要把节点端点暴露给不可信网络。原生工具也不等于操作系统沙箱。
- **自修改机制已有，持续自我改进仍待验证。** 当前不是每个组件都有自己的 Agent，Settings 和 Provider 配置界面仍在。
- **改造仍需验证。** 共享契约变化需要协调，Electron 外层更新不同于节点替换；回退进程也不会自动撤回数据库变更或外部动作。
- **协议兼容性不含糊。** MAGI peer 子集参考 A2A 1.0，但不是符合 A2A 标准的实现。

具体见[安全说明](SECURITY.md)、[验证指南](docs/verification.md)和[未完成工作](docs/future.md)。

## 继续了解与参与

- [文档导航](docs/README.md)：按使用、改造、验证选择阅读路线。
- [架构](docs/architecture.md)：进程、BUS、源码归属与变更激活。
- [开发贡献指南](CONTRIBUTING.md)：构建、测试与提交聚焦的上游改动。
- [研究方向](docs/future.md)：还有哪些事情需要实现或证明。

欢迎人类与 Agent 一起参与。较大的上游改动，先用 Issue 对齐范围；你自己的 fork，方向由你决定。
安全问题请按[安全说明](SECURITY.md)报告，不要放进公开 Issue。

**一千个人，就有一千个 MAGI。**

## 许可

MAGI 以 [Business Source License 1.1](LICENSE) 作为 source-available 软件发布，
许可人为 realTaki。允许非生产使用；Additional Use Grant 还允许条款所列的学术研究、
教育、评估与个人非商业生产使用。每个版本的前六个月内，商业生产使用须向 realTaki
购买单独的书面商业许可。商业许可事宜请通过[本仓库](https://github.com/realTaki/MAGI)
联系 realTaki。

每个版本自许可人首次公开发布满六个月后转为 [MIT](https://opensource.org/license/mit)。
重新分发或修改该版本不会重置其转换日期。转换前，副本及衍生作品继续受 BSL 约束；
这不是 OSI 认可的开源许可，也不代表具有 GPL 式的源码提供义务。转换后，MIT 允许
商业使用及闭源衍生作品，但须保留适用的版权与许可声明。第三方及贡献者的声明仍然适用。
此次更新不撤销此前已发布版本已授予的权利。具体以 [LICENSE](LICENSE) 条款为准。

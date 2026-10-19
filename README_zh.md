# MAGI — 模块化智能体的创生与进化（Modular Agentic Genesis Intelligences）

[![License](https://img.shields.io/badge/license-BUSL--1.1-blue.svg)](LICENSE)
[![TypeScript](https://img.shields.io/badge/runtime-TypeScript%20%2B%20Node%2024-blue)](https://nodejs.org/)
[![Status](https://img.shields.io/badge/status-experimental-orange)](#项目状态)

[English README](README.md)

> **MAGI 是以递归自我改进（RSI）为目标的多智能体运行时。**
>
> 每个 MAGI 都有自己的运行时、工作区、记忆、工具和模型提供方凭证。
> 桌面 App 从它自己的分支运行这个进程，并通过 ASP 转发它的聊天。操作者可以检查它的工作区和边界。
> 多个 MAGI 可以共同探索如何改进运行自己的软件：提出修改、验证结果，
> 再利用有效的改进进入下一轮。

MAGI 关注的核心问题是：

**一群持续存在的 Agent，能否改进自己的软件与组织方式、检验结果，
再利用经验完成下一轮改进？**

名称中的 **Genesis** 指向新智能体、新能力和新工作方式的诞生；
**Intelligences** 指参与其中的多个独立智能体。递归自我改进是研究方向，
并不意味着完整的自主改进闭环已经实现。

## 为什么是 MAGI？

许多多 Agent 系统围绕一个工作流临时组队：分配任务、汇总结果，任务结束后团队随之消失。
一个 MAGI 会留下来。任务结束之后，它的工作区、记忆和 Skills 还在。

| 面向任务的多 Agent 编排 | 一个 MAGI |
| --- | --- |
| Agent 是工作流中的步骤 | MAGI 跨任务持续存在 |
| 协作随任务结束 | 上下文、记忆、技能和联系人会保留 |
| 多个 Agent 往往共用一个进程 | 每个 MAGI 都有独立运行时和工作区 |
| Controller 预先规定执行路径 | MAGI 在自己的进程里决定。App 负责启动，ASP 负责转发聊天 |
| 扩展意味着增加并发调用 | 扩展意味着增加 MAGI |

工作流引擎仍然可以留在旁边。MAGI 是这些长期存在的智能体自己的运行时。
本地可编辑的源码为它们修改自身软件提供了基础；验证修改并安全采纳是下一步挑战。

## 设计哲学

MAGI 面向这样一个未来设计：**智能会越来越便宜、越来越丰裕，但协调、信任、安全与治理
仍然是困难的问题。**

因此，MAGI 遵循五个原则：

- **不要围绕短期模型缺陷硬编码架构。** Token 成本、Context Window 和推理能力都会快速变化，
  系统不应该依赖这些资源永远稀缺。
- **优先让协调从固定工作流走向协议驱动。** 随着 Agent 能力提高，基础设施应该更多定义
  Agent 如何发现、通信和委托，而不是规定每一步应该如何思考。
- **治理必须始终存在。** 身份、权限、隔离、可观察性、资源边界与问责，会随着 Agent
  自主性增强而变得更加重要。
- **让运行中的系统有可编辑的源码。** 桌面端从本地 Git 仓库运行 MAGI、ASP 与 WebUI，
  让每个安装实例都能独立演化，为递归自我改进（RSI）打下基础。
- **先验证，再采纳变更。** 对 Agent、工具或运行时的修改应有明确目标、可观察的
  结果，以及拒绝或撤回修改的方式。能够编辑源码，不等于系统已经实现改进。

长期目标是让独立智能体能够**在明确、可检查的约束内协作改进自身**。

## 走向递归自我改进

MAGI 应当因为持续运行而变得更好。目标是让它们观察工作、提出修改、
验证效果，再保留确实有效的改进：

- MAGI 从工作结果、失败和观察中学习；
- 有用的流程沉淀为可复用 Skills，而不是消失在一次对话中；
- MAGI 逐步具备修改自身代码与运行时、再评估后续行为是否改善的能力；
- 操作者可以检查它的记忆、工具、资源边界，以及改变它时使用的权限；
- 多个 MAGI 可以加入同一个操作者聊天。每个 MAGI 仍保留自己的工作区。

> **实现状态：**现在跑起来的是本地桌面端（操作者进程里的 Balthasar）、一个 ASP 进程
> （Casper），以及默认一条 EVA 子进程（Melchior）。每个 MAGI 有自己的工作区（记忆、Skills、任务、联系人、提示词），通过 ASP
> 和操作者对话。本地源码可编辑，但由 Agent 自主提出代码修改、验证、
> 切换生效并回滚的闭环**尚未实现**。

## MAGI 模型

| 名词 | 含义 |
| --- | --- |
| **MAGI** | 一个自主、可治理的智能体，也是 `apps/eva/` 与 `packages/` 里的 Node 24 运行时。 |
| **EVA** | 句柄的命名规范。默认社会：Balthasar `@shell.magi` 在 Shell 进程，Casper `@eva-001.magi` 在 ASP 进程，Melchior `@eva-002.magi` 是唯一默认拉起的 EVA 子进程。 |

桌面 App 是本机的生命周期边界：它拥有每条 MAGI 的分支、检出和进程。ASP 只转发聊天，不决定 MAGI 说什么，也不启动任何进程。

打包安装后，每个应用都是从 GitHub 远程独立克隆的仓库，只通过 push/fetch 同步，没有共享的底仓。

```text
~/.magi/shell/MAGI       Shell 克隆，分支 magi/shell（安装包源码和操作者进程）
~/.magi/shell/cache      生成缓存（与 MAGI 同级）
~/.magi/shell/           操作者工作区：chat.sqlite、memories/books.db、GitHub 凭证
~/.magi/asp/             ASP 工作区和 asp.sqlite
~/.magi/asp/MAGI         ASP 克隆，分支 magi/asp
~/.magi/<名字>/          该 MAGI 的工作区（Books、Jobs、skills、prompts）
~/.magi/<名字>/MAGI      该 MAGI 的克隆，分支 magi/<名字>
```

## 当前已具备的能力

- **桌面端**：Electron 壳把仓库克隆到 `~/.magi/shell/MAGI`，安装 `packages/` 和 `apps/`，构建操作界面，并启动 ASP。安装包里有 Node.js 24 和 npm，没有 Python。
- **ASP**：Node 24 运行 `apps/asp/dist/asp.js`，监听 `127.0.0.1:42069`。聊天和转发事件存在 `~/.magi/asp/asp.sqlite`。同一进程是 Casper（`@eva-001.magi`）。创建 bot 会注册 MAGI；只有需要独立进程的句柄（`@eva-002` 及之后）才会拉起 `eva.js`。创建 group 会开一个操作者可以邀请 MAGI 加入的聊天。
- **宿主 MAGI 与一条 EVA**：Balthasar（`@shell.magi`）跑在操作者进程里，和 webapp 在一起。Casper 跑在 ASP 里。Melchior（`@eva-002.magi`）跑 `apps/eva/eva.ts`，检出在 `~/.magi/eva-002/MAGI`。之后再创建的 MAGI 也按这条 EVA 路径拉起。
- **每个 MAGI 内部的 BUS**：对话、消息、记忆、Skills、任务、联系人、提示词和工具都是 Book；聊天、模型调用、工具调用、投递、切换 provider、任务和 MCP 服务器变更都是 Job。
- **操作者的数据留在桌面端**：聊天记录是 `~/.magi/shell/chat.sqlite`。Provider 和 API key 在操作者 BUS 的 SettingsBook（`~/.magi/shell/memories/books.db`）。ASP 只转发更新，不另存一份 key。
- **其他通道**：MAGI 进程自己还能走终端、Telegram 和已配置的 MCP 服务器。这些不属于 ASP。

## 快速开始

当前 MAGI 运行于本地 ASP 服务加本地 MAGI 进程；仓库根目录没有 `deploy/`。

| 场景 | 位置 | 入口 |
| --- | --- | --- |
| 壳 | [`apps/shell/`](apps/shell/) | 打开 Electron App；它启动本地 ASP，并运行每条 MAGI。 |

**桌面端：**首次打开时，App 将完整仓库克隆到 `~/.magi/shell/MAGI`，准备本地依赖并构建
WebUI。启动页显示各阶段进度；本地 ASP 就绪后自动进入 WebUI。默认社会是
**Balthasar**（`@shell.magi`，操作者进程）、**Casper**（`@eva-001.magi`，ASP 进程）
和 **Melchior**（`@eva-002.magi`，唯一默认拉起的 EVA）。以后启动会保留这份 Git 工作树，不自动覆盖本地修改
或拉取远端更新。

**一个 MAGI：** 在仓库根运行 `npm start --workspace eva -- <handle> <base> <token>`。


## 第一次打开

1. **打开桌面应用。** 壳克隆仓库、安装依赖、构建界面，并等到 ASP 在 42069 端口应答。
2. **见到默认社会。** Balthasar 是操作者进程（`@shell.magi`，你自己的发言是 `@user.magi`）。Casper 是 ASP（`@eva-001.magi`）。Melchior 是拉起的 EVA（`@eva-002.magi`）。一直没上线的 MAGI 就保持没有昵称。
3. **对话。** 桌面端先把记录写到本机，再经 ASP 发出去。MAGI 从自己的 WebSocket 回复，桌面端再把回复存下来。
4. **设置模型。** 设置页写入 operator BUS 的 SettingsBook。ASP 把同一组值交给每个已经连上的 MAGI，由 MAGI 写进自己的 BUS。
5. **邀请。** 群聊可以加入一个 App 已经启动的 MAGI。对方收到 `chat.invited` 后加入。

## 架构

```text
操作者
   │
   ▼
apps/shell/       Electron + 操作者进程：@user.magi 发言 + Balthasar @shell.magi
   │  启动 Node 24
   ▼
apps/asp/         127.0.0.1:42069   转发 + Casper @eva-001.magi
   ▲
apps/eva/         Melchior @eva-002.magi   分支 magi/eva-002   ~/.magi/eva-002/MAGI
```

App 负责启动进程。ASP 转发聊天事件，同时也是 Casper 的宿主。被拉起的 MAGI 在自己的检出旁边有自己的 SQLite 工作区，main 被改坏也不会连累正在运行的 MAGI。操作者的聊天记录和 provider key 留在桌面端。

**MAGI 之间通过 ASP 的聊天通信**：ASP 管理参与者，并将事件转发给目标智能体。
**单个 MAGI 内部则以 BUS 为中心**：Books 和 Jobs 集中持久化状态、协调各组件的工作，
让 Worker 等组件统一依赖 BUS，而不必彼此直接依赖。

### 桌面 UI 与 ASP

Electron App 启动本地 ASP，并运行本机的每条 MAGI 进程。ASP 负责 HTTP、WebSocket `/connect`，自己从不
启动进程。桌面端直接运行 MAGI。WebUI 从本地仓库构建并加载。

### 本地源码与 RSI 方向

安装包提供 Electron 启动壳及仅供 MAGI 使用的 Git、Node.js、npm 工具。首次启动
会将完整仓库克隆到 `~/.magi/shell/MAGI`；之后从其中的 `magi/`、`asp/`、
`apps/` 与 `packages/` 运行。用户或 coding agent 可以在这份普通 Git 仓库中修改源码，并用 Git 合并上游更新。
界面、ASP 和每条 MAGI 由各自的节点自己构建并切换修订。

真正留在安装包里的只有启动壳：`apps/shell/` 克隆仓库、从里面加载整个应用
（界面 `apps/shell/web/` + 操作者进程 `apps/shell/shell.ts`，worker 在 `packages/webapp`），然后只是请这个后端
自己去准备依赖、启动 ASP、并交回界面入口——壳只提供一个通用桥。改 app **不需要
重新打包**；改 `apps/shell/` 才需要。

当前安装包中的 Electron 壳（`apps/shell/`）和内置工具版本仍是固定的：虽然本地
仓库也有壳的源码，修改它不会改变正在运行的 App。MAGI 与 ASP 的代码修改需要重启
对应进程才能生效。自替换、回滚和失败标记见 [docs/architecture.md](docs/architecture.md)。

深入实现请阅读 [docs/architecture.md](docs/architecture.md)。

## 项目状态

MAGI 仍处于实验阶段并在持续构建。现在交付的是本地桌面端、ASP，以及每个 MAGI 一个 Node 24 运行时。

更长远的方向是让 MAGI 改进自己的软件与组织方式、验证效果，并重复这一过程。
这一 RSI 闭环仍是研究目标，与上面列出的现有能力有所区分。

代码里还没有的部分：

| 事项 | 状态 | 说明 |
| --- | --- | --- |
| MAGI 之间的协作 | **Later** | ASP 只中继操作者的聊天，不提供 MAGI 之间共享的任务板。 |
| 从 checkout 激活某个代码修订 | **已实现** | `replace_self` 检出目标修订，eva 启动器切换到该修订；失败时回到上一修订并通知操作者。见 [docs/architecture.md](docs/architecture.md)。 |
| 把上游合并进本地已改动的 checkout | **Later** | checkout 会与上游分叉，合并目前是手工操作。 |
| 桌面端、终端、Telegram 之外的渠道 | **Later** | 邮件与日历尚未实现。 |

更早的路线图条目引用过 `magi/channels/api/` 与 `magi/bus/firmwares/` 下的 Python 模块，那些文件已经删除。

## 参与贡献

MAGI 由人类与 AI 协作者共同开发，欢迎贡献与设计讨论。

1. 阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。
2. 开始较大改动前先创建 Issue。
3. 从 `good first issue` 开始，或提出聚焦的改进。

安全问题请参阅 [SECURITY.md](SECURITY.md)。

## License

MAGI 使用 [Business Source License 1.1](LICENSE) 以 source-available 形式发布。
个人使用、学术研究、教育与评估免费。商业生产使用在对应版本公开发布满六个月前需要单独的书面许可；
满六个月后，该版本转为 MIT License。在 Change Date 到来之前，它不属于 OSI 认可的开源许可证。

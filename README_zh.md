# MAGI — 模块化、可治理的智能体集合（Modular Agentic Governed Intelligences）

[![License](https://img.shields.io/badge/license-BUSL--1.1-blue.svg)](LICENSE)
[![TypeScript](https://img.shields.io/badge/runtime-TypeScript%20%2B%20Bun-blue)](https://bun.sh)
[![Status](https://img.shields.io/badge/status-experimental-orange)](#项目状态)

[English README](README.md)

> **MAGI 是面向持久化、模块化、可治理智能体社会的运行时。**
>
> MAGIS（MAGI Society）是由独立 MAGI 组成的持久组织。每个 MAGI 都有自己的运行时、
> 工作区、记忆、工具、模型提供方凭证，
> 以及在 Society 中的角色。它们在 Society 中协作，通过独立管理的 MAGI Runtime
> 执行工作，保留经验，并在不放弃边界、问责与操作者控制的前提下，逐步成长为持久的群体智能。

MAGI 关注的核心问题是：

**怎样让一群 AI Agent 拥有身份、连续性和组织结构，能够随时间共同进步，
同时让这种自主性始终可观察、有边界、可治理？**

## 为什么是 MAGI？

许多多 Agent 系统围绕一个工作流临时组队：分配任务、汇总结果，任务结束后团队随之消失。
MAGI 把**组织本身**作为第一等对象。

| 面向任务的多 Agent 编排 | MAGI Society 运行时 |
| --- | --- |
| Agent 是工作流中的步骤 | MAGI 是组织中持久存在的成员 |
| 协作随任务结束 | 上下文、记忆、技能和关系会保留 |
| 多个 Agent 往往共用一个进程 | 每个 MAGI 都有独立运行时和工作区 |
| Controller 预先规定执行路径 | Society 负责协调，基础设施负责生命周期和边界 |
| 扩展意味着增加并发调用 | 扩展意味着增加有能力的 MAGI 与相互连接的 Society |

MAGI 不取代工作流引擎。它提供的是一层基础设施，让长期存在的 Agent 组织能够运行、
学习、重组，并逐渐承担更多自主协调。

## 设计哲学

MAGI 面向这样一个未来设计：**智能会越来越便宜、越来越丰裕，但协调、信任、安全与治理
仍然是困难的问题。**

因此，MAGI 遵循四个原则：

- **不要围绕短期模型缺陷硬编码架构。** Token 成本、Context Window 和推理能力都会快速变化，
  系统不应该依赖这些资源永远稀缺。
- **优先让协调从固定工作流走向协议驱动。** 随着 Agent 能力提高，基础设施应该更多定义
  Agent 如何发现、通信和委托，而不是规定每一步应该如何思考。
- **治理必须始终存在。** 身份、权限、隔离、可观察性、资源边界与问责，会随着 Agent
  自主性增强而变得更加重要。
- **让运行中的系统有可编辑的源码。** 桌面端从本地 Git 仓库运行 MAGI、ASP 与 WebUI，
  让每个安装实例都能独立演化，为递归自我改进（RSI）打下基础。

长期目标是构建一种基础设施，让自主智能体能够
**在明确、可检查的约束内自由协作**。

## 走向可治理的群体智能

最终目标是让一个 MAGIS 因为真实存在过而变得更好，
同时始终保持可观察、可约束、可治理：

- MAGI 从工作结果、失败和观察中学习；
- 有用的流程沉淀为可复用 Skills，而不是消失在一次对话中；
- ADAM 识别能力缺口，组织专业 EVA，并随着工作变化调整 Society；
- Society 可以共享知识、互相协作，而不把成员简化为无状态 API 调用；
- 操作者始终能够检查组织、记忆、工具、资源边界，以及改变组织时使用的权限。

> **实现状态：**现在跑起来的是本地桌面端、一个 ASP 进程，以及每个 MAGI 一个 Bun
> 进程。每个 MAGI 有自己的工作区（记忆、Skills、任务、联系人、提示词），通过 ASP
> 和操作者对话。Society 树、ADAM 控制面、MAGI 之间的任务板是设计目标，**这份代码里还没有**。

## MAGI 模型

这些名称有明确分工：

| 名词 | 含义 |
| --- | --- |
| **MAGI** | 系统中自主、可治理 Agent 的总称。 |
| **MAGIS** | **MAGI Society**：由 MAGI 组成的组织；Society 可以形成树。 |
| **MAGIC** | 过去给单个 MAGI 用的内部名字。当前运行时没有 MAGIC 表。 |
| **ADAM** | Society 的领导 MAGI，提供控制面并协调其他 MAGI。 |
| **EVA** | 执行工作的 MAGI 角色；一个 Society 可以创建、配置、启动、停止和退役多个 EVA。 |

ADAM 和 EVA 是将来 Society 里的角色名。现在的应用还没有 Society 树，也没有 ADAM
控制面。ASP 是本机的生命周期边界：它启动 MAGI 进程并转发会话，不决定 MAGI 说什么。

## 当前已具备的能力

- **桌面端**：Electron 壳把仓库克隆到 `~/.magi/MAGI`，安装 `asp/` 和 `magi/`，构建操作界面，并启动 ASP。安装包里有 Node.js 24、npm 和 Bun，没有 Python。
- **ASP**：Node 24 运行 `asp/main.ts`，监听 `127.0.0.1:42069`。会话和转发事件存在 `~/.magi/asp/asp.sqlite`。创建 bot 会拉起对应 MAGI；创建 group 会开一个操作者可以邀请 MAGI 加入的会话。
- **每个 MAGI 一个进程**：Bun 运行 `magi/magi.ts`。默认工作区是 `~/.magi/<名字>`。新路径还不存在时，仍会打开旧的 `~/.magi/ts-magi/<名字>`。
- **每个 MAGI 内部的 BUS**：对话、消息、记忆、Skills、任务、联系人、提示词和工具都是 Book；聊天、模型调用、工具调用、投递、切换 provider、任务和 MCP 服务器变更都是 Job。
- **操作者的数据留在桌面端**：聊天记录是 `~/.magi/app/chat.sqlite`。Provider 和 API key 在 `~/.magi/app/provider.json`。ASP 只转发更新，不另存一份 key。
- **其他通道**：MAGI 进程自己还能走终端、Telegram 和已配置的 MCP 服务器。这些不属于 ASP。

## 快速开始

当前 MAGI 运行于本地 ASP 服务加本地 MAGI 进程；仓库根目录没有 `deploy/`。

| 场景 | 位置 | 入口 |
| --- | --- | --- |
| 壳 | [`shell/`](shell/) | 打开 Electron App；它启动本地 ASP，由 ASP 启动 MAGI。 |

**桌面端：**首次打开时，App 将完整仓库克隆到 `~/.magi/MAGI`，准备本地依赖并构建
WebUI。启动页显示各阶段进度；本地 ASP 就绪后自动进入 WebUI。如果社会里一个 MAGI
都还没有，本地后端会先把前三个建出来——**MELCHIOR**、**BALTHASAR**、**CASPER**
（ASP 分配 `eva-000/001/002`）。以后启动会保留这份 Git 工作树，不自动覆盖本地修改
或拉取远端更新。

**一个 MAGI：** 在 `magi/` 中运行 `bun run start -- <handle> <base> <token>`。


## 第一次打开

1. **打开桌面应用。** 壳克隆仓库、安装依赖、构建界面，并等到 ASP 在 42069 端口应答。
2. **见到前三个 MAGI。** 一个都没有时，应用会创建 `eva-000`、`eva-001`、`eva-002`，并尝试把它们叫做 **MELCHIOR**、**BALTHASAR**、**CASPER**。一直没上线的 MAGI 就保持没有昵称。
3. **对话。** 桌面端先把记录写到本机，再经 ASP 发出去。MAGI 从自己的 WebSocket 回复，桌面端再把回复存下来。
4. **设置模型。** 设置页写入 `~/.magi/app/provider.json`。ASP 把同一组值交给每个已经连上的 MAGI，由 MAGI 写进自己的 BUS。
5. **邀请。** 群会话可以加入一个 ASP 已经启动的 MAGI。对方收到 `session.invited` 后加入。

## 架构

```text
操作者
   │
   ▼
shell/            Electron 窗口
   │  加载
app/              操作界面和本地后端
   │  启动 Node 24
   ▼
asp/              127.0.0.1:42069
   │  拉起 Bun
   ├── magi  eva-000     ~/.magi/eva-000
   ├── magi  eva-001     ~/.magi/eva-001
   └── magi  eva-002     ~/.magi/eva-002
```

ASP 负责启动进程和转发会话事件，不是 MAGI 思考的地方。每个 MAGI 有自己的 SQLite 工作区。操作者的聊天记录和 provider key 留在桌面端。

### 桌面 UI 与 ASP

Electron App 启动本地 ASP；ASP 负责 HTTP、WebSocket `/connect` 与 MAGI 进程
创建。桌面端不直接启动 MAGI。WebUI 从本地仓库构建并加载。

### 本地源码与 RSI 方向

安装包提供 Electron 启动壳及仅供 MAGI 使用的 Git、Node.js、Bun 工具。首次启动
会将完整仓库克隆到 `~/.magi/MAGI`；之后从其中的 `magi/`、`asp/`、
`app/` 运行。用户或 coding agent 可以在这份普通 Git 仓库中修改源码、
重新构建界面，并用 Git 合并上游更新。`app/dist/index.html` 变化后，
桌面端会询问是否重新加载，不会擅自刷新界面。

真正留在安装包里的只有启动壳：`shell/` 克隆仓库、从里面加载整个应用
（界面 `app/src/` + 本地后端 `app/main/`），然后只是请这个后端
自己去准备依赖、启动 ASP、并交回界面入口——壳只提供一个通用桥。改 app **不需要
重新打包**；改 `shell/` 才需要。

当前安装包中的 Electron 壳（`shell/`）和内置工具版本仍是固定的：虽然本地
仓库也有壳的源码，修改它不会改变正在运行的 App。MAGI 与 ASP 的代码修改需要重启
对应进程才能生效。本地可编辑源码是 RSI 的基础；MAGI 尚未实现对自身代码修订的
自动验证、切换、重启和回滚。详见[壳的说明](shell/README.md)。

深入实现请阅读：

- [架构](docs/ARCHITECTURE.md)
- [关键业务流程](docs/business-flows.md)
- [术语与 ID 命名规范](docs/terms.md)
- [ASP](asp/README.md)
- [路线图](docs/ROADMAP.md)

## 项目状态

MAGI 仍处于实验阶段并在持续构建。现在交付的是本地桌面端、ASP，以及每个 MAGI 一个 Bun 运行时。上文说的 Society 树和 MAGI 之间的协作，这份仓库里还没有。

更大的方向——自主学习、协议驱动协作、更丰富的策略执行，以及逐渐具备自组织能力的
**可治理智能体集合（governed intelligences）**——是公开的项目愿景。README 会明确区分
这些长期方向与当前已经交付的能力，让 MAGI 保持足够的野心，同时不混淆 Roadmap 与实现状态。

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

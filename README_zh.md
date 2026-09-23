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

> **实现状态：**持久记忆、Skills、MAGIS/MAGI 模型、隔离的 EVA 生命周期管理、
> 受限控制面、以及同一 MAGIS 内 MAGI 之间的 **A2A 持久 actor effect** 协作
> （`notify` / `request` 两类单向终态 + MAGIS 协作目录 + `message_magi` 工具）
> 都已经存在。跨 MAGI 的自主学习、能力评估、自主组织重构、更丰富的策略执行、
> 以及 Society 间知识交换都是正在推进的设计目标，**目前并非全部已经实现**。

## MAGI 模型

这些名称有明确分工：

| 名词 | 含义 |
| --- | --- |
| **MAGI** | 系统中自主、可治理 Agent 的总称。 |
| **MAGIS** | **MAGI Society**：由 MAGI 组成的组织；Society 可以形成树。 |
| **MAGIC** | 内部表/API 中单个 MAGI 的名称，不是另一个产品概念。 |
| **ADAM** | Society 的领导 MAGI，提供控制面并协调其他 MAGI。 |
| **EVA** | 执行工作的 MAGI 角色；一个 Society 可以创建、配置、启动、停止和退役多个 EVA。 |

```text
操作者
   │ WebUI
   ▼
MAGIS：Engineering
   │
   ├── ADAM / MAGI                     控制面与协调者
   │      └── Society 的持久记忆、策略与关系
   │
   ├── EVA / MAGI                      独立运行时 + 工作区
   ├── EVA / MAGI                      独立运行时 + 工作区
   └── 子 MAGIS：Research              自己的 ADAM 与 MAGI

MAGIS 共享数据库（同一 Society 内 MAGI 之间）：
   a2a_request_job_board   ─┐
   a2a_notify_job_board    ─┴─► AgentWorker（目标 magi_id）持久 actor effect
                              message_magi {magi_id, mode, text, deadline_seconds}
```

ADAM 是协调者，而不是不受限制的宿主机管理员。ASP 服务拥有生命周期操作，
只启动范围受限的本地 MAGI 进程。

## 当前已具备的能力

- **独立运行时**：ADAM 与每个 EVA 都是独立本地进程，并有自己的工作区。
- **组织管理**：WebUI 管理 MAGIS 树与 MAGI，包括 ADAM 指派和 EVA provider 配置。
- **EVA 生命周期控制**：操作者创建 bot 时，由 ASP 服务启动本地 MAGI 进程。
- **持久化运行记忆**：对话历史、联系人知识、任务状态和可搜索记忆跨对话保留。
- **通道与工具**：已有 WebUI；Telegram、MCP server、Skills、定时任务和内置工具扩展 MAGI 的能力。
- **Provider 独立性**：MAGI 持有各自的 provider 配置和 API 凭证，而非共享一个全局模型账户。
- **MAGIS 内 A2A 协作**：同一 MAGIS 的 MAGI 之间通过**持久 actor effect** 直接协作：
  消息落在 MAGIS 共享数据库的两张 job board 上（`a2a_request_job_board`
  一次 request / 一次 response，`a2a_notify_job_board` 单向持久通知），
  由目标 MAGI 的 `AgentWorker` 直接消费；不经过 HTTP / webhook / 外部签名协议。
  工具契约收敛为 `message_magi({magi_id, mode ∈ {notify, request}, text, deadline_seconds})`。
  每个 MAGI 的 `responsibility`（职责说明）与同 MAGIS 成员目录会随每轮
  system prompt 渲染给 LLM，让模型在选择协作对象时能直接看到边界与专长。

## 快速开始

当前 MAGI 运行于本地 ASP 服务加本地 MAGI 进程；仓库根目录没有 `deploy/`。

| 场景 | 位置 | 入口 |
| --- | --- | --- |
| 桌面客户端 | [`desktop/`](desktop/) | 打开 Electron App；它启动本地 ASP，由 ASP 启动 MAGI。 |

**桌面端：**首次打开时，App 将完整仓库克隆到 `~/.magi/MAGI`，准备本地依赖并构建
WebUI。启动页显示各阶段进度；本地 ASP 就绪后自动进入 WebUI。如果社会里一个 MAGI
都还没有，本地后端会先把前三个建出来——**MELCHIOR**、**BALTHASAR**、**CASPER**
（ASP 分配 `eva-000/001/002`）。以后启动会保留这份 Git 工作树，不自动覆盖本地修改
或拉取远端更新。

**一个 MAGI：** 在 `ts-magi/` 中运行 `bun run start -- <handle> <base> <token>`。


## 从第一个 MAGIS 到组织成长

1. **初始化 Genesis**：`magi init` provision 根 MAGI Society
   （Genesis），再创建第一个 MAGI（**`eva-000`**），并让它担任 Genesis 的 ADAM。
2. **Onboard 操作者**：配置管理员访问和 Society 要使用的通道。
3. **塑造组织**：在 WebUI 创建子 MAGIS，并指派其 ADAM MAGI。
4. **增加能力**：配置 EVA 的 provider 与凭证，然后让 ADAM 通过 orchestrator 启动或停止它。
5. **积累智能**：对话、任务结果、联系人、记忆和可复用 Skills 留在 Society 中，而非随一次请求丢弃。
6. **治理自主性**：随着 Society 成长，让生命周期权限、凭证、工作区和操作者可见的边界保持明确，
   而不是把所有 MAGI 合并进一个不受限制的进程。

## 架构

```text
                        ┌─────────────────────────────┐
                        │            操作者           │
                        │             WebUI           │
                        └──────────────┬──────────────┘
                                       │
                        ┌──────────────▼──────────────┐
                        │         ADAM / MAGI         │
                        │        Society 控制面       │
                        └──────────────┬──────────────┘
                                       │ 生命周期请求
                        ┌──────────────▼──────────────┐
                        │          MAGI ASP           │
                        │       本地进程启动器        │
                        └───────┬──────────────┬───────┘
                                │              │
                     ┌──────────▼───┐  ┌──────▼──────────┐
                     │ EVA / MAGI   │  │ EVA / MAGI      │
                     │   本地进程   │  │    本地进程     │
                     └──────────────┘  └─────────────────┘
```

ASP 是**生命周期权限边界，而不是 Society 的“思考大脑”**。它启动本地进程；
每个 MAGI 仍保有自己的 Runtime、状态、工具和 Society 角色，并各自维护本地 SQLite 工作区。

### 桌面 UI 与 ASP

Electron App 启动本地 ASP；ASP 负责 HTTP、WebSocket `/connect` 与 MAGI 进程
创建。桌面端不直接启动 MAGI。WebUI 从本地仓库构建并加载。

### 本地源码与 RSI 方向

安装包提供 Electron 启动壳及仅供 MAGI 使用的 Git、Node.js、Bun 工具。首次启动
会将完整仓库克隆到 `~/.magi/MAGI`；之后从其中的 `ts-magi/`、`magi-asp/`、
`desktop/app/` 运行。用户或 coding agent 可以在这份普通 Git 仓库中修改源码、
重新构建界面，并用 Git 合并上游更新。`desktop/app/dist/index.html` 变化后，
桌面端会询问是否重新加载，不会擅自刷新界面。

真正留在安装包里的只有启动壳：`desktop/shell/` 克隆仓库、从里面加载整个应用
（界面 `desktop/app/src/` + 本地后端 `desktop/app/main/`），然后只是请这个后端
自己去准备依赖、启动 ASP、并交回界面入口——壳只提供一个通用桥。改 app **不需要
重新打包**；改 `desktop/shell/` 才需要。

当前安装包中的 Electron 壳（`desktop/shell/`）和内置工具版本仍是固定的：虽然本地
仓库也有壳的源码，修改它不会改变正在运行的 App。MAGI 与 ASP 的代码修改需要重启
对应进程才能生效。本地可编辑源码是 RSI 的基础；MAGI 尚未实现对自身代码修订的
自动验证、切换、重启和回滚。详见[桌面端说明](desktop/README.md)。

深入实现请阅读：

- [架构](docs/ARCHITECTURE.md)
- [关键业务流程](docs/business-flows.md)
- [术语与 ID 命名规范](docs/terms.md)
- [magi-asp](magi-asp/README.md)
- [路线图](docs/ROADMAP.md)

## 项目状态

MAGI 仍处于实验阶段并在持续构建。现有代码已经提供 Society 建模、onboarding、
隔离节点部署、持久 Runtime 状态和 EVA 生命周期控制等基础能力。

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

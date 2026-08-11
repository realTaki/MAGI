# MAGI — 模块化、可治理的智能体集合（Modular Agentic Governed Intelligences）

[![License](https://img.shields.io/badge/license-BUSL--1.1-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://python.org)
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

因此，MAGI 遵循三个原则：

- **不要围绕短期模型缺陷硬编码架构。** Token 成本、Context Window 和推理能力都会快速变化，
  系统不应该依赖这些资源永远稀缺。
- **优先让协调从固定工作流走向协议驱动。** 随着 Agent 能力提高，基础设施应该更多定义
  Agent 如何发现、通信和委托，而不是规定每一步应该如何思考。
- **治理必须始终存在。** 身份、权限、隔离、可观察性、资源边界与问责，会随着 Agent
  自主性增强而变得更加重要。

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

ADAM 是协调者，而不是不受限制的宿主机管理员。它不会获得宿主机 Docker socket
或宽泛的 Kubernetes 权限，而是通过受限、认证的 orchestrator 请求生命周期变更。
控制面只会创建 MAGI 所需范围内的私有工作区与 Runtime，并按需创建 MAGIS 的
PostgreSQL 与公共工作区资源。

## 当前已具备的能力

- **独立运行时**：ADAM 与每个 EVA 都是独立 Kubernetes Deployment，并有自己的持久化工作区。
- **组织管理**：WebUI 管理 MAGIS 树与 MAGI，包括 ADAM 指派和 EVA provider 配置。
- **EVA 生命周期控制**：ADAM 可经由集群内 orchestrator 请求启动、停止与删除 EVA。
- **持久化运行记忆**：会话历史、联系人知识、任务状态和可搜索记忆跨会话保留。
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

按你的场景选一条部署路径。三种路径都同等支持并位于 `deploy/` 下，
所有启动代码都收口在 `magi.startup`：

| 场景 | 路径 | 入口 |
| --- | --- | --- |
| 我只想在单机上跑一个 MAGI | [deploy/cli/](deploy/cli/) | `./deploy/cli/install.sh`（安装、初始化并启动） |
| 我在迭代 k8s 模块化方案 | [deploy/k8s-dev/](deploy/k8s-dev/) | `./deploy/k8s-dev/bootstrap-k8s-dev.sh` |
| 我有现成集群，要部署上去 | [deploy/k8s/](deploy/k8s/) | `./deploy/k8s/bootstrap-k8s.sh` |

**单机本地**是上手最快的一条：直接跑在宿主上（没有 Docker，也没有 k8s），
状态放在 `~/.magi/`（Linux）或 `~/Documents/.magi/`（macOS、Windows）。只需运行
`./deploy/cli/install.sh`：它会安装 MAGI，provision 第一个 MAGI（`eva-000`）和根
MAGI Society **Genesis**（让 `eva-000` 担任 ADAM），并启动 Runtime 与 WebUI。打开
[http://127.0.0.1:42069](http://127.0.0.1:42069)，先选择正在运行的 MAGI，再完成
onboarding。以后只需执行 `magi start`，它会保留现有状态并恢复未运行的服务。需要新 MAGI 时，运行
`magi node create --name eva-001`、`magi node run --name eva-001` 即可；
每个新 MAGI 都是独立 OS 进程。

**k8s-dev** 会在本地启动一个 `kind` 集群并部署第一个开发 MAGI 节点。
宿主机只需要 Docker。脚本会按需下载固定版本的 `kind` 与 `kubectl`、
构建镜像、创建集群，并以后端 reload 与 Vite HMR 部署开发节点。
开发部署会挂载：

```text
宿主仓库                                   → /app/magi     源码热加载
workspace PVC（挂到容器根 /）              → /MAGI_Citizens/<name>  (由 HOST_WORKSPACE_DIR=/ + MAGI_NAME 推导)
MAGIS 公共工作区 PVC                        → /magis
```

K8s Pod **不传** `HOST_WORKSPACE_DIR`：路径解析器检测到
`KUBERNETES_SERVICE_HOST` 后默认宿主根为 `/`；PVC 挂到容器根后，
`MAGI_Citizens/<name>` 由 `MAGI_NAME` 推导并直接落到 PVC 上。

已有 Kubernetes 集群或生产式部署可使用：

```bash
MAGI_IMAGE=registry.example.com/your-team/magi:0.1.0 \
  ./deploy/k8s/bootstrap-k8s.sh
```

镜像、存储、网络、Secret 与环境配置请见各部署路径下的 README。

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
                                       │ 经认证的生命周期请求
                        ┌──────────────▼──────────────┐
                        │       MAGI Orchestrator     │
                        │     受限的 Kubernetes API   │
                        └───────┬──────────────┬───────┘
                                │              │
                     ┌──────────▼───┐  ┌──────▼──────────┐
                     │ EVA / MAGI   │  │ EVA / MAGI      │
                     │ Deployment   │  │ Deployment      │
                     │ PVC + Secret │  │ PVC + Secret    │
                     └──────────────┘  └─────────────────┘
```

Orchestrator 是**生命周期权限边界，而不是 Society 的“思考大脑”**。
它只负责把需要基础设施权限的操作限制在狭窄、可认证的执行边界内；
每个 MAGI 仍然保有自己的 Runtime、状态、工具和 Society 角色。

Kubernetes 是当前部署目标：它为每个 MAGI 提供明确的运行边界，也让 orchestrator
能管理隔离资源，而不必让 ADAM 成为集群管理员。每个 MAGI 保留私有、单副本的 SQLite
工作区，落在 `/MAGI_Citizens/<MAGI_NAME>/memories/magi.db`——K8s Pod **不传**
`HOST_WORKSPACE_DIR`，路径解析器检测 `KUBERNETES_SERVICE_HOST` 自动默认宿主根为 `/`，
PVC 挂到容器根，`MAGI_Citizens/<name>` 由 `MAGI_NAME` 推导；每个 MAGIS 则有独立
PostgreSQL 与公共工作区 PVC，承载组织事实和团队共享文件。启动契约只有四个变量
（`HOST_WORKSPACE_DIR`、`MAGI_NAME`、`MAGIS_DATABASE_URL`、`MAGI_ID`），
workspace 路径由它们推导，调用方不传入。精确边界见
[存储边界](docs/ARCHITECTURE.md#storage-ownership) 与
[BUS 运行时架构](docs/MAGI_BUS_CENTRIC_ARCHITECTURE.md)。

### 一个 WebUI、一个镜像

MAGI 只发布一个容器镜像，但提供两个可选服务角色。默认 `magi` 命令运行单个
MAGI，并只提供集群内 Runtime API；`magi webui` 则运行唯一的 React 控制台、登录、
组织控制面，以及到当前所选 MAGI 的受控代理。浏览器始终只访问一个 WebUI Service，
不会直接连接任何 MAGI Pod。

落地页先选择正在运行的 MAGI，再只显示该 MAGI 的直接 MAGIS Admin 与 assigned user。
代理用 `MAGI_CONTROL_SECRET` 为每个内部请求签名；签名同时绑定目标 MAGI 与已认证身份。
运行时会拒绝发给其他 MAGI 的请求。切换 MAGI 必须重新登录，不能在已登录页面直接切换。
MAGI 已配置自己的 Bot 时自行发送验证码；首次尚未配置 Bot 时，才由其直接 MAGIS 的 ADAM
Bot 代发验证码。

深入实现请阅读：

- [架构](docs/ARCHITECTURE.md)
- [关键业务流程](docs/business-flows.md)
- [数据库与迁移说明](docs/database-migrations.md)
- [部署总览](deploy/README.md)
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

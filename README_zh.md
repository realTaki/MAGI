# MAGI — 模块化代理群体智能

[![License](https://img.shields.io/badge/license-BUSL--1.1-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://python.org)
[![Status](https://img.shields.io/badge/status-experimental-orange)](#项目状态)

[English README](README.md)

> **MAGI 是面向持久化、模块化智能体社会的运行时。**
>
> MAGIS（一个 MAGI Society）不是一次性的群聊或任务流水线，而是由独立 MAGI Citizens
> 组成的组织。每个 MAGI Citizen 都有自己的运行时、工作区、记忆、工具、模型提供方凭证，
> 以及在 Society 中的角色。它们由 Adam 协调、由 EVE 执行工作，保留经验，逐步成长为
> 持久的群体智能。

MAGI 要回答的不只是「怎样把一个任务分配给多个 Agent」，而是：

**怎样让一群 AI Agent 拥有身份、连续性、组织结构，并且能够随时间共同进步？**

## 为什么是 MAGI？

许多多 Agent 系统围绕一个工作流临时组队：分配研究任务、汇总结果、任务结束后团队
随之消失。MAGI 把**组织本身**作为第一等对象。

| 面向任务的多 Agent 编排 | MAGI Society 运行时 |
| --- | --- |
| Agent 是工作流中的步骤 | MAGI Citizen 是组织中持久存在的成员 |
| 协作随任务结束 | 上下文、记忆、技能和关系会保留 |
| 多个 Agent 往往共用一个进程 | 每个 MAGI Citizen 都有独立的容器化运行时和工作区 |
| 管理者派发预定义任务 | Adam 协调 Society，EVE 可被独立配置、启动与停止 |
| 扩展是增加并发调用 | 扩展是增加 MAGI Citizen 和相互连接的 Society |

MAGI 不取代工作流引擎；它提供让长期存在的 Agent 组织得以运行、学习与演化的基础。

## 走向群体智能

最终目标不是一个反复委派 prompt 的静态层级，而是让一个 MAGIS 因为真实存在过
而变得更好：

- MAGI Citizens 从工作结果、失败和观察中学习；
- 有用的流程沉淀为可复用 Skills，而不是消失在一次对话中；
- Adam 识别能力缺口，组织专业 EVE，并随着工作变化调整 Society；
- Society 可以共享知识、互相协作，而不把成员简化为无状态 API 调用；
- 操作者始终能够检查组织、记忆、工具，以及改变组织时使用的权限。

> **实现状态：**持久记忆、Skills、Society/MAGI Citizen 模型和隔离的 EVE 生命周期管理
> 已经构成当前基础。跨 MAGI Citizen 的自主学习、能力评估、自主组织重构，以及 Society 间
> 知识交换都是正在设计的目标，**目前尚未实现**。

## MAGI 模型

这些名称有明确分工：

| 名词 | 含义 |
| --- | --- |
| **MAGI** | 系统中自主 Agent 的总称。 |
| **MAGIS** | **MAGI Society**：由 MAGI Citizens 组成的组织；Society 可以形成树。 |
| **MAGIC** | **MAGI Citizen**：属于一个 Society 的单个 Agent。 |
| **Adam** | Society 的领导 MAGI Citizen，提供控制面并协调其他 MAGI Citizens。 |
| **EVE** | 执行工作的 MAGI Citizen；一个 Society 可以创建、配置、启动、停止和退役多个 EVE。 |

```text
操作者
   │ WebUI
   ▼
MAGIS：Engineering
   │
   ├── Adam / MAGI Citizen             控制面与协调者
   │      └── Society 的持久记忆、策略与关系
   │
   ├── EVE / MAGI Citizen              独立运行时 + 工作区
   ├── EVE / MAGI Citizen              独立运行时 + 工作区
   └── 子 MAGIS：Research                自己的 Adam 与 MAGI Citizens
```

Adam 不会获得宿主机 Docker socket 或宽泛的 Kubernetes 权限。它通过受限、认证的
orchestrator 请求 EVE 生命周期变更；控制面只会创建该 EVE 所需范围内的 Deployment、
PVC 和 provider Secret。

## 当前已具备的能力

- **独立运行时**：Adam 与每个 EVE 都是独立 Kubernetes Deployment，并有自己的持久化工作区。
- **组织管理**：WebUI 管理 MAGIS 树与 MAGI Citizens，包括 Adam 指派和 EVE provider 配置。
- **EVE 生命周期控制**：Adam 可经由集群内 orchestrator 请求启动、停止与删除 EVE。
- **持久化运行记忆**：会话历史、联系人知识、任务状态和可搜索记忆跨会话保留。
- **通道与工具**：已有 WebUI；Telegram、MCP server、Skills、定时任务和内置工具扩展 MAGI Citizen 的能力。
- **Provider 独立性**：MAGI Citizen 持有各自的 provider 配置和 API 凭证，而非共享一个全局模型账户。

## 快速开始：本地开发集群

最快的路径会启动本地 `kind` 集群和第一个开发 MAGI 节点。宿主机只需要 Docker。
脚本会按需下载固定版本的 `kind` 与 `kubectl`、构建镜像、创建集群，并以后端 reload 与
Vite HMR 部署开发节点。

```bash
git clone https://github.com/realTaki/MAGI.git
cd MAGI
./deploy/bootstrap-local.sh
```

打开 [http://127.0.0.1:42069](http://127.0.0.1:42069)，完成 onboarding。系统初始化时，
会自动创建根 MAGI Society（**Genesis**），然后创建第一个 MAGI Citizen（**EVA-00 PROTO TYPE**），
并让它担任 Genesis 的 Adam。

本地开发部署会挂载：

```text
宿主仓库             → /app/magi        源码热加载
workspace/eva00      → /workspace       开发实例的持久化工作区
```

已有 Kubernetes 集群或生产式部署可使用：

```bash
MAGI_IMAGE=registry.example.com/your-team/magi:0.1.0 \
  ./deploy/bootstrap-k8s.sh
```

镜像、存储、网络、Secret 与环境配置请见 [Kubernetes 部署指南](deploy/k8s/README.md)。

## 从第一个 MAGIS 到组织成长

1. **初始化 Genesis**：系统先创建根 MAGI Society（Genesis），再创建第一个 MAGI Citizen
   （**EVA-00 PROTO TYPE**），并让它担任 Genesis 的 Adam。
2. **Onboard 操作者**：配置管理员访问和 Society 要使用的通道。
3. **塑造组织**：在 WebUI 创建子 MAGIS，并指派其 Adam MAGI Citizens。
4. **增加能力**：配置 EVE 的 provider 与凭证，然后让 Adam 通过 orchestrator 启动或停止它。
5. **积累智能**：对话、任务结果、联系人、记忆和可复用 Skills 留在 Society 中，而非随一次请求丢弃。

## 架构

```text
                        ┌─────────────────────────────┐
                        │            操作者           │
                        │             WebUI           │
                        └──────────────┬──────────────┘
                                       │
                        ┌──────────────▼──────────────┐
                        │         Adam / MAGI Citizen         │
                        │       Society 控制面          │
                        └──────────────┬──────────────┘
                                       │ 经认证的生命周期请求
                        ┌──────────────▼──────────────┐
                        │       MAGI Orchestrator      │
                        │   受限的 Kubernetes API      │
                        └───────┬──────────────┬───────┘
                                │              │
                     ┌──────────▼───┐  ┌──────▼──────────┐
                     │ EVE / MAGI Citizen  │  │ EVE / MAGI Citizen     │
                     │ Deployment   │  │ Deployment      │
                     │ PVC + Secret │  │ PVC + Secret    │
                     └──────────────┘  └─────────────────┘
```

Kubernetes 是当前部署目标：它为每个 MAGI Citizen 提供明确的运行边界，也让 orchestrator
能管理隔离资源，而不必让 Adam 成为集群管理员。当前单副本部署使用 SQLite；共享数据库
是后续面向更大规模集群的路径。

深入实现请阅读：

- [架构](docs/ARCHITECTURE.md)
- [关键业务流程](docs/business-flows.md)
- [数据库与迁移说明](docs/database-migrations.md)
- [Kubernetes 部署](deploy/k8s/README.md)
- [路线图](docs/ROADMAP.md)

## 项目状态

MAGI 仍处于实验阶段并在持续构建。现有代码已提供 Society 建模、onboarding、隔离节点部署
和 EVE 生命周期控制；上文的群体智能机制是公开的项目愿景，并已明确标注其尚未实现，
以避免混淆路线图和已交付能力。

## 参与贡献

MAGI 由人类与 AI 协作者共同开发，欢迎贡献与设计讨论。

1. 阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。
2. 开始较大改动前先创建 Issue。
3. 从 `good first issue` 开始，或提出聚焦的改进建议。

安全问题请见 [SECURITY.md](SECURITY.md)。

## 许可证

MAGI 采用 [Business Source License 1.1](LICENSE)，以 source-available
方式发布。个人使用、学术研究、教育和评估可免费使用；在某一版本首次公开发布满六个月
之前，商业生产使用须取得 Licensor 的书面商业授权。该版本满六个月后，将依 MIT License
开放商用。在 Change Date 前，本项目不是 OSI 定义的开源软件。

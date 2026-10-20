# MAGI — 模块化智能体的创生与进化（Modular Agentic Genesis Intelligences）

[![License](https://img.shields.io/badge/license-BUSL--1.1-blue.svg)](LICENSE)
[![TypeScript](https://img.shields.io/badge/runtime-TypeScript%20%2B%20Node%2024-blue)](https://nodejs.org/)
[![Status](https://img.shields.io/badge/status-experimental-orange)](docs/architecture.md)

[English README](README.md)

> **MAGI 是以递归自我改进（RSI）为目标的多智能体运行时。**
>
> 每个 MAGI 都有自己的运行时、工作区、记忆、工具和模型提供方凭证。
> 每个 EVA 运行自己的服务并直接交换签名消息；桌面 App 启动内置 Shell EVA。操作者可以检查各节点的工作区和边界。
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
| Controller 预先规定执行路径 | MAGI 在自己的进程里决定，并通过对等协议通信 |
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
- **让运行中的系统有可编辑的源码。** 桌面端从本地 Git 仓库运行 Shell EVA 与 WebUI，
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

> **实现状态：**当前是实验性的本地桌面和多节点运行时。节点可修改自己的可编辑源码、
> 验证候选并请求启动器切换修订；这不等于已经验证自主改进带来持续收益，也不代表整个演示已验收。

## 当前运行方式

| 角色 | Handle | 运行位置 |
| --- | --- | --- |
| 内置 Shell EVA | `@shell.magi` | Electron 启动的 Shell launcher 的 child |
| 按需创建的本地 EVA | 由创建请求确定 | 独立 launcher、child、工作区与服务端点 |
| 远程 EVA | 对方公开的显示 Handle | 对方自己的服务，由签名公钥指纹识别 |
| 操作者 | `@user.magi` | 人类发言身份，不是另一个节点进程 |

打开桌面应用只启动内置 Shell EVA，不要求 ASP，也不会自动创建默认的其他 EVA。
操作者通过 Shell 聊天创建本地 EVA，或提供远程 EVA 的 URL。
连接使用签名 hello；对方记录待接受联系人，并自行决定是否接受。收到 URL 不会自动回访或授予权限。
本地进程通过 `apps/eva/eva.mjs --role …` 启动；类导出文件不是服务 CLI。

每个节点拥有自己的服务端点、contactBook、BUS、记忆、Skills、任务、提示词、Provider 设置和密钥。
EVA 直接交换签名聊天消息；群聊使用共同的会话 UUID，各参与者独立保存历史并同步原始签名事件。
Provider 验证与保存由接收节点通过所有者连接执行，远程聊天不具有配置或进程管理权限。
[MAGI peer 协议子集](packages/channel-peer/README.md) 参考了 A2A 1.0，但不是符合该标准的实现。
群聊的一致性边界见[群聊同步设计](docs/architecture/peer-group-sync.md)。
旧 ASP 源码仍保留，但不属于当前启动必需路径。

`MAGI_HOME` 是节点数据根目录，默认 `~/.magi`。Shell 使用 `shell/`，其他节点使用其本地名称目录；
已有的节点数据不会因此改名或删除。托管源码放在节点自己的 `MAGI/` 中，构建修订放在 `revisions/` 中。
源码启动使用经核验的开发检出，安装包则准备托管 Shell 检出。
Electron 用户数据与节点工作区是不同存储，不能仅凭 `MAGI_HOME` 就认定整个桌面 profile 已隔离。

## 阅读入口

- [启动、配置与常见故障](docs/running.md)：完整命令、启动顺序、环境变量与独立节点。
- [开发贡献指南](CONTRIBUTING.md)：根 lockfile、MAGI 自带 Node/npm 与源码准备。
- [架构](docs/architecture.md)：launcher/child、BUS、对等通信、可编辑源码与运行修订的区别。
- [验证指南](docs/verification.md)：必要检查与真实桌面验收的边界。
- [Provider 目录](docs/provider-catalog.md)：字段、模型目录和验证流程。
- [文档索引](docs/README.md)、[研究与未完成工作](docs/future.md)。

修改本地源码、构建并切换节点修订、更新 Electron 安装包是不同操作。
本地自重建不要求 commit 或 push；修改 Electron 主进程、preload 或启动壳则需要退出重开，
安装包场景还需构建并安装新的包。具体边界见架构文档。

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

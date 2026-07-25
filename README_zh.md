# MAGI — Modular Agentic Group Intelligence（模块化代理群体智能）

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://python.org)
[![Status](https://img.shields.io/badge/status-experimental-orange)](#)

一个**由自治、可独立部署的代理**组成的群体智能系统。这些代理我们统称为
**MAGI**。系统里的每一个代理——控制面、个人助理、未来加入的协调者——都是
一个 MAGI。系统整体能力来自这些 MAGI 之间的协同方式。

它**不是**一款 2B 聊天产品，**不是** SaaS 聊天机器人，也**不是**代码
编写工具。MAGI 是**面向"一群代理"的运行时基础设施**：一个镜像、一份
runtime，按位置需要配置成对应的 archetype（原型）。今天我们交付两种
原型——**Adam** 与 **EVE**——但整套架构是为"未来会有第三、第四种原型"
留好接口的。

> **⚠️ 本项目完全由 AI 编写和维护。** 目前处于早期实验阶段，可能包含 bug、
> 功能不完整或行为异常。在生产环境或类生产环境中使用请自行承担风险。
> 欢迎贡献和提交 bug 报告。

长期计划（C0–C8）见 [docs/ROADMAP.md](docs/ROADMAP.md)。

---

## 为什么叫"群体智能"

单个 agent 哪怕再强，也只是一个视角。MAGI 的押注是：**有用的形态是一组
agent 在协同**——一个 manager-原型的 MAGI 持有共享状态并编排其它 MAGI，
加上一支 worker-原型的 MAGI 队伍，每位绑定一个 **User** 并针对该 User
的上下文做调优。Adam 与 EVE 是今天交付的两种原型；runtime 里没有任何
东西把它们硬编码成"永远只有这两种"。

一个 workspace 可以跑：

- **一个 Adam**（manager / 控制面 MAGI，默认通道 WebUI），加
- **若干 EVE**：每位 **Enhanced Virtual Expert** 绑定到一位 assigned User
  （默认通道 Telegram）。

一个 workspace 也可以只跑 Adam（运营控制台）、只跑一个 EVE（单 User
试点），或者未来只跑某个第三种原型——runtime 都不在乎。

---

## 命名与架构

| 名称 | 原型 | 角色 |
|---|---|---|
| **MAGI** | — | 整个系统：一群协同的 MAGI。 |
| **Adam** | **manager**（*Adaptive Distributed Agent Matrix*） | 控制面 MAGI。给 operator 提供 **Web 前端**，持有 system of record（通讯录、设置、审计、技能），可以**派发 / 创建 / 回收** worker MAGI。默认通道：**WebUI**。 |
| **EVE** | **worker**（*Enhanced Virtual Expert*） | 绑定到一位 **assigned User** 的 worker-原型 MAGI。默认通道：**Telegram**。从 Adam 拉取 workspace 级数据（通讯录、设置、workspace 技能）并本地缓存。 |
| *operator* | 用户角色 | 使用 Adam Web UI 操作 workspace 的人类。**有意小写**；取代旧的 "admin" / "HR / IT" 表述。 |

### 每个 agent 都是一个 MAGI

Adam 与 EVE 跑的是**同一份 MAGI runtime**（代理循环、动态上下文、技能
运行器、主动引擎、LLM 提供者、审计、通道分发器）。进程镜像只有一个；
archetype 在启动时确定。每一个架构选择都是独立的配置轴——没有任何轴
被 archetype 硬编码：

| 轴 | 环境变量 | 按 archetype 的默认值 | 说明 |
|---|---|---|---|
| 原型 / 权限范围 | `MAGI_NODE_ROLE` | `adam` = manager，`eve` = worker | archetype **唯一**决定的事。影响运行时内部的策略门。 |
| 通道 | `MAGI_CHANNELS` | `adam` → `webui`，`eve` → `telegram` | 逗号分隔列表。Adam 也可以挂载 Telegram；EVE 也可以挂载 WebUI。 |
| 状态后端 | `MAGI_STATE_BACKEND` | `auto`（设置 `DATABASE_URL` 则用 Postgres，否则 SQLite） | 与 archetype 无关。任何 MAGI 都可用 Postgres 或 SQLite。 |
| Adam 对等节点 | `MAGI_ADAM_URL` | `http://adam:42069` | 始终读取。任何需要 Adam RPC（审计、配置拉取）的 MAGI 都设置此项。 |
| LLM 提供者 | `ANTHROPIC_API_KEY` 等 | 未设置 | 按节点或全局配置。 |

archetype 只决定权限范围和少量默认字段；每个底层轴都是可覆盖的。
`magi.node.run()` 不会按 `MAGI_NODE_ROLE` 分支——它遍历通道列表并分发给
每个通道的启动器。

> 完整的架构、部署拓扑、RPC 协议与 Phase 1 构建计划在
> [docs/ROADMAP.md](docs/ROADMAP.md) 里指向的规划笔记中。
> 本 README 只覆盖运行代码所需的内容。

---

## 范围（明确约束）

- **无 CLI。** 所有运维 / 管理工作均在 Adam 的 Web UI 中完成。调度 / 回收
  背后的 Docker 编排对 operator 不可见。
- **worker MAGI 默认不互相直调。** EVE 之间的协同走 manager MAGI（今天：
  Adam）。一个 worker 只跟自己的 Adam 和自己的 assigned User 通信。
  **未来原型可能放宽这条**——例如一个 project-MAGI 在 worker 之间做 broker
  ——而无需改 runtime。
- **WebUI 只是另一个通道。** 它是 `channels/webui/` 适配器；Telegram 是
  `channels/telegram/` 适配器。两者实现相同的 `Channel` 接口，将消息送入
  同一个运行时代理循环。

---

## 仓库布局

扁平布局——包位于仓库根目录，无 `src/` 包装。

```
magi/
├── __init__.py
├── __main__.py     # 单一入口点。验证 MAGI_NODE_ROLE，分发至 magi.node。
├── runtime/        # 共享核心：代理循环、上下文、技能、主动引擎、LLM、审计。
│                   # Adam 与 EVE 跑同一份 runtime；仅通道、权限范围、状态后端不同。
├── channels/       # 可插拔通道适配器。任何 archetype 均可挂载任意子集。
│   ├── base.py     # Channel 协议——两个适配器均实现此接口。
│   ├── telegram/   # python-telegram-bot v21+（C3+）。
│   └── webui/      # FastAPI + HTMX（CRUD）+ WS（聊天控制台，C7+）。
│       └── app.py  # FastAPI 应用；由 `webui` 启动器懒加载。
└── node/           # 节点组装：一个 NodeConfig，一个 check()，一个 run()。
    └── __init__.py # 无基于 archetype 的代码路径。遍历 MAGI_CHANNELS，依次启动。
tests/              # 单元测试 / 集成测试 / 端到端测试（每个检查点一个 e2e 文件）。
```

一个控制台脚本：

| 脚本 | 角色 |
|---|---|
| `magi` | 启动 MAGI 节点。`MAGI_NODE_ROLE` 选择 archetype 预设（`adam` = manager，`eve` = worker）；`MAGI_CHANNELS`、`MAGI_STATE_BACKEND` 等覆盖各轴的默认值。 |

---

## 快速开始（本地开发，Phase C0）

Phase C0 仅验证项目结构、单一入口点和 Adam 的 `/health` 端点是否正常工作。
实际功能（User 注册、TG 机器人、LLM 调用、审计、调度 UI）将在后续检查点
中实现。

### 前置条件
- Python ≥ 3.12
- [`uv`](https://docs.astral.sh/uv/) ≥ 0.11

### 安装
```bash
uv sync --extra adam --extra eve
```

### 运行节点（运行时选择 archetype）
```bash
# EVE（桩）——打印解析后的配置并退出
MAGI_NODE_ROLE=eve uv run magi --check

# Adam——在 :42069 启动 FastAPI
MAGI_NODE_ROLE=adam uv run magi
# 在另一个终端：
curl http://127.0.0.1:42069/health
# → {"status":"ok","service":"magi","version":"0.1.0"}
```

### 使用 Docker Compose 运行（完整本地环境）
```bash
cp .env.example .env
# 编辑 MAGI_SHARED_SECRET 以及你想启用的 LLM 提供者密钥
docker compose up --build
# Adam 位于 http://localhost:42069/health
# Postgres 位于 localhost:5432（用户名/密码：magi/magi，数据库：magi）
```

Compose 文件目前仅运行 `postgres` + `adam`。每位 User 的 `eve-<id>`
服务将在检查点 C6 中与 Adam Web UI 中的调度按钮一同接入——两者均从同一个
Dockerfile 构建，仅通过 `MAGI_NODE_ROLE` 区分。

---

## Phase 1 路线图

九个可演示的检查点（小型团队约四周）：

| # | 检查点 | 演示内容 |
|---|---|---|
| C0 | 骨架——uv 项目，单一入口点 | `curl /health` → 200 |
| C1 | Adam WebUI 上的 User / EVE / 技能注册管理 | 在浏览器中创建 / 编辑 / 删除 |
| C2 | 通过一次性验证码绑定 Telegram ID | 在真实 TG 账号上发送验证码 |
| C3 | 通道抽象 + TG 通道 + 配置拉取 | 真实对话往返 |
| C4 | 技能加载器 + 4 个 MVP 技能（范围感知） | "下午 3 点提醒我"、"搜索知识库" |
| C5 | 主动提醒（APScheduler + 引擎） | 提醒触发 + 审计 |
| C6 | 通过 Adam Web UI 调度 / 回收（Docker SDK） | 启动 / 销毁 EVE |
| C7 | 控制台（通过 WebUI 通道的聊天式 SPA） | 实时事件流 |
| C8 | 加固——哈希链、快照、发件箱容量 | 关掉 Adam，EVE 继续运行 |

完整检查清单请参见计划文件。

---

## 治理说明

MAGI 将审计视为一等关注点：每条通道消息的收发（无论哪个通道——WebUI
或 Telegram）、每一次技能调用和每一次 operator 操作都会记录到
`audit_log`（不可变，哈希链）或 `event_log`（高基数，带 TTL）中。技能
执行边界从一开始就是 JSON-in / JSON-out，因此后续阶段可以收紧沙箱而
无需重构。EVE 容器将配置本地缓存，在 Adam 不可达时以降级模式运行——
本地部署意味着 Adam 重启是常态，而非例外。

---

## 术语表（新提法）

- **MAGI** — Modular Agentic Group Intelligence。整套系统；也是自治
  的单元。**每个 agent 都是一个 MAGI**。
- **MAGIC** — Modular Agentic Group Intelligence Council。一个组织
  （表 `magics`）。一个 Magi 恰好属于一个 MAGIC。
- **Magi** — 一个 MAGI agent（表 `magis`）。每个 Magi 在它所属的
  MAGIC 里持有一个 **position**：`adam`（leader，每个 MAGIC 一个）或
  `eve`（member，N 个）。position 是组织结构的事实，**不是**关于
  服务关系。
- **Position（职位）** — `adam` / `eve`。写在 `magis.position`。运行
  时读这个决定"我能做什么"（ADAM：管理这个 MAGIC；EVE：服务它的
  assigned User）。**与谁登录无关**。
- **Workspace** — 包含一个 Adam + 它指挥的若干 EVE + 那些 EVE 的
  Users + 共享技能 + 审计日志的运营边界。取代旧的"公司 / enterprise"
  提法。
- **User** — MAGI 认识的一类人（表 `users`，由 `contact_entries` 改
  名而来）。持有一种 **role**：`admin` / `assigned` / `user` / `guest`。
  这是 person 与某个 MAGI 服务关系的事实；**与 MAGI 的 `position` 无关**。
- **Role** — `admin` / `assigned` / `user` / `guest`。写在 `users.role`。
  `admin` = 操作员（能进 Adam Web UI）；`assigned` = 被 EVE 服务的人；
  `user` = 未绑定的 org 成员；`guest` = 外部。

### Schema 形态（reframe 之后）

Schema 收敛成**三张表 + 一张 IM 绑定表**。"agent-centered" 框架
保留：**MAGI agent** 是一等公民行；**人**是挂在 agent 上的叶子；
**MAGIC** 是 agent 所属的组织。

| 表 | 持有 | 关键列 | 取值 |
|---|---|---|---|
| `magics` | 组织（council） | n/a | — |
| `magis` | agent（MAGI 运行时实体） | `position` | `adam` / `eve` |
| `users` | 人（原 `contact_entries` 改名而来） | `role` | `admin` / `assigned` / `user` / `guest` |
| `magi_im_bindings` | 每个 MAGI 在每个 channel 上的 IM 身份 | n/a | — |

**两条正交的轴**——不要混：

- **`magis.position`** 是 agent 在 MAGIC 组织结构里的固有角色。
  **每个 MAGIC 恰好 1 个 ADAM + N 个 EVE**，由 partial UNIQUE
  强制。这是组织结构的事实，**不是**由"哪个 User 登录"决定的。
- **`users.role`** 描述这个 person 与某个 MAGI 的服务关系。
  `admin` = 操作员；`assigned` = 被服务的人；`user` / `guest` =
  未绑定的 org 成员 / 外部。v0 一个 User 最多绑到一个 Magi（通过
  `users.magi_id` FK）；未来需要 multi-MAGI 绑定，加
  `user_magi_bindings` junction。

Schema 迁移已完成：`employees` + `contact_entries` + `user_im_bindings`
三表合一为 `contacts`；`departments` 被 `magics`（MAGI 团队）替代。
详见 [docs/ROADMAP.md](docs/ROADMAP.md) 的 "Post-refactor follow-ups"。

文档级 vs 代码级跟进项的完整 backlog 见
[docs/ROADMAP.md](docs/ROADMAP.md) 中"Post-refactor follow-ups"一节。

## 参与贡献

MAGI 处于实验阶段，欢迎贡献代码。参与方式：

1. 阅读 [CONTRIBUTING.md](CONTRIBUTING.md)
2. 写代码前先开 Issue 沟通 — 避免方向不对
3. 挑选 `good first issue` 或提出你的想法

代码规范、提交格式和 PR checklist 见 [CONTRIBUTING.md](CONTRIBUTING.md)。
所有参与者须遵守 [行为准则](CODE_OF_CONDUCT.md)。

安全漏洞请私下报告 — 见 [SECURITY.md](SECURITY.md)。

## 许可证

MIT — 见 [LICENSE](LICENSE)。
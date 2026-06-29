# MAGI — Modular Agentic Governed Intelligence（模块化可治理智能代理系统）

一个本地化的企业代理系统，每位员工都拥有一个专属的
**EVE**（*Everyday Virtual Employee*，每日虚拟员工）——一个通过 Telegram
处理日常沟通、信息整理、提醒、后续跟进和流程推送的个人代理。

该产品**不是** SaaS 聊天机器人，也**不是**代码编写工具。它以本地部署方式运行，
包含一个 **Adam** 节点（面向 HR 的 Web 前端控制/编排后端）以及每位员工一个
Docker 容器（EVE），从一开始就内置了严格的治理机制（审计、RBAC、哈希链日志）。

> **⚠️ 本项目完全由 AI 编写和维护。** 目前处于早期实验阶段，可能包含 bug、
> 功能不完整或行为异常。在生产环境或类生产环境中使用请自行承担风险。
> 欢迎贡献和提交 bug 报告。

## 命名与架构

| 名称       | 角色                                                                                  |
|------------|---------------------------------------------------------------------------------------|
| **MAGI**   | 整个系统。                                                                           |
| **Adam**   | 企业端节点。为 HR/管理员提供 **Web 前端**，用于操作一切（员工管理、技能注册、EVE 调度/回收、审计、状态查看）。默认通道：**WebUI**。 |
| **EVE**    | 员工端节点，每位员工一个。默认通道：**Telegram**。从 Adam 拉取企业级数据（通讯录、设置、企业技能）并本地缓存。 |
| *admin*    | 使用 Adam Web UI 的用户角色（HR/IT）。有意使用小写。                                  |

**Adam 和 EVE 是同一个节点。** 共享一个 `magiruntime` 包（代理循环、动态上下文、技能运行器、主动引擎、LLM 提供者、审计）和一个进程镜像。每个架构选择都是独立的配置轴——没有任何轴因角色而硬编码：

| 轴               | 环境变量                  | 按角色的默认值          | 说明                                                              |
|------------------|--------------------------|--------------------------|--------------------------------------------------------------------|
| 权限范围         | `MAGI_NODE_ROLE`         | `adam` = 企业级，`eve` = 个人级 | 角色**唯一**决定的东西。影响运行时内部的策略门。 |
| 通道             | `MAGI_CHANNELS`          | `adam` → `webui`，`eve` → `telegram`  | 逗号分隔列表。Adam 也可以挂载 Telegram；EVE 也可以挂载 WebUI。 |
| 状态后端         | `MAGI_STATE_BACKEND`     | `auto`（设置 `DATABASE_URL` 则用 Postgres，否则用 SQLite） | 与角色无关。如需共享存储，EVE 也可用 Postgres；Adam 开发环境也可用 SQLite。 |
| Adam 对等节点    | `MAGI_ADAM_URL`          | `http://adam:8000`       | 始终读取。任何需要 Adam RPC（审计、配置拉取）的节点都设置此项。 |
| LLM 提供者       | `ANTHROPIC_API_KEY` 等   | 未设置                    | 按节点或全局配置。                                                 |

角色仅设置权限范围和少量默认字段；每个底层轴都是可覆盖的。`magi.node.run()` 不会按 `role` 分支——它遍历通道列表并分发给每个通道的启动器。

> 完整的架构、部署拓扑、RPC 协议和 Phase 1 构建计划详见
> [`.claude/plans/linked-cooking-waffle.md`](.claude/plans/linked-cooking-waffle.md)。
> 本 README 仅涵盖运行代码所需的内容。

## 范围（明确约束）

- **无 CLI。** 所有运维/管理工作均在 Adam 的 Web UI 中完成。调度/回收背后的 Docker 编排对运维人员不可见。
- **EVE 实例之间不互相通信。** 每个 EVE 仅与 Adam 及其自己的员工通过 Telegram 通信。任何跨员工协调都在 Adam 中进行。
- **WebUI 只是另一个通道。** 它是 `channels/webui/` 适配器；Telegram 是 `channels/telegram/` 适配器。两者实现相同的 `Channel` 接口，将消息送入同一个运行时代理循环。

---

## 仓库布局

扁平布局——包位于仓库根目录，无 `src/` 包装。

```
magi/
├── __init__.py
├── __main__.py     # 单一入口点。验证 MAGI_NODE_ROLE，分发至 magi.node。
├── runtime/        # 共享核心：代理循环、上下文、技能、主动引擎、LLM、审计。
│                   # Adam 和 EVE 运行同一个 runtime；仅通道、权限范围、状态后端不同。
├── channels/       # 可插拔通道适配器。任一角色均可挂载任意子集。
│   ├── base.py     # Channel 协议——两个适配器均实现此接口。
│   ├── telegram/   # python-telegram-bot v21+（C3+）。
│   └── webui/      # FastAPI + HTMX（CRUD）+ WS（聊天控制台，C7+）。
│       └── app.py  # FastAPI 应用；由 `webui` 启动器懒加载。
└── node/           # 节点组装：一个 NodeConfig，一个 check()，一个 run()。
    └── __init__.py # 无基于角色的代码路径。遍历 MAGI_CHANNELS，依次启动。
tests/              # 单元测试 / 集成测试 / 端到端测试（每个检查点一个 e2e 文件）。
```

一个控制台脚本：

| 脚本    | 角色                                                                                                                       |
|---------|----------------------------------------------------------------------------------------------------------------------------|
| `magi`  | 启动 MAGI 节点。`MAGI_NODE_ROLE` 选择权限范围预设；`MAGI_CHANNELS`、`MAGI_STATE_BACKEND` 等覆盖各轴的默认值。 |

---

## 快速开始（本地开发，Phase C0）

Phase C0 仅验证项目结构、单一入口点和 Adam 的 `/health` 端点是否正常工作。实际功能（员工管理、TG 机器人、LLM 调用、审计、调度 UI）将在后续检查点中实现。

### 前置条件
- Python ≥ 3.12
- [`uv`](https://docs.astral.sh/uv/) ≥ 0.11

### 安装
```bash
uv sync --extra adam --extra eve
```

### 运行节点（运行时选择角色）
```bash
# EVE（桩）——打印解析后的配置并退出
MAGI_NODE_ROLE=eve uv run magi --check

# Adam——在 :8000 启动 FastAPI
MAGI_NODE_ROLE=adam uv run magi
# 在另一个终端：
curl http://127.0.0.1:8000/health
# → {"status":"ok","service":"adam","version":"0.1.0"}
```

### 使用 Docker Compose 运行（完整本地环境）
```bash
cp .env.example .env
# 编辑 MAGI_SHARED_SECRET 以及你想启用的 LLM 提供者密钥
docker compose up --build
# Adam 位于 http://localhost:8000/health
# Postgres 位于 localhost:5432（用户名/密码：magi/magi，数据库：magi）
```

Compose 文件目前仅运行 `postgres` + `adam`。每位员工的 `eve-<id>` 服务将在检查点 C6
中与 Adam Web UI 中的调度按钮一同接入——两者均从同一个 Dockerfile 构建，
仅通过 `MAGI_NODE_ROLE` 区分。

---

## Phase 1 路线图

九个可演示的检查点（小型团队约四周）：

| #  | 检查点                                              | 演示内容                            |
|----|-----------------------------------------------------|-------------------------------------|
| C0 | 骨架——uv 项目，单一入口点                           | `curl /health` → 200                |
| C1 | Adam WebUI 上的员工/EVE/技能注册管理                | 在浏览器中创建/编辑/删除            |
| C2 | 通过一次性验证码绑定 Telegram ID                    | 在真实 TG 账号上发送验证码          |
| C3 | 通道抽象 + TG 通道 + 配置拉取                       | 真实对话往返                        |
| C4 | 技能加载器 + 4 个 MVP 技能（范围感知）              | "下午 3 点提醒我"、"搜索知识库"     |
| C5 | 主动提醒（APScheduler + 引擎）                      | 提醒触发 + 审计                     |
| C6 | 通过 Adam Web UI 调度/回收（Docker SDK）            | 启动/销毁 EVE                       |
| C7 | 控制台（通过 WebUI 通道的聊天式 SPA）               | 实时事件流                          |
| C8 | 加固——哈希链、快照、发件箱容量                      | 关掉 Adam，EVE 继续运行             |

完整检查清单请参见计划文件。

---

## 治理说明

MAGI 将审计视为一等关注点：每条通道消息的收发（无论哪个通道——WebUI 或 Telegram）、
每一次技能调用和每一次管理员操作都会记录到 `audit_log`（不可变，哈希链）或
`event_log`（高基数，带 TTL）中。技能执行边界从一开始就是 JSON-in/JSON-out，
因此后续阶段可以收紧沙箱而无需重构。EVE 容器将配置本地缓存，
在 Adam 不可达时以降级模式运行——本地部署意味着 Adam 重启是常态，而非例外。

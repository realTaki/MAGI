# MAGI 数据库迁移

MAGI 的应用数据库由 Alembic 管理，SQLite 的 schema 版本记录在：

```text
alembic_version
```

配置文件：

```text
alembic.ini                      # 仓库根；生产镜像复制到 /app/alembic.ini
```

迁移脚本位置：

```text
magi/agent/db/alembic/versions/  # 各 revision；script_location = magi/agent/db/alembic
```

运行时由 `magi/agent/db/alembic_runner.py` 调用 Alembic（`upgrade head` /
`stamp`），`engine.init_orm` 在节点启动时自动触发，无需手动执行。

## 当前迁移链

HEAD = `0006_contact_notes`。完整链按 `down_revision` 串联：

```text
0001_baseline
   └─ 0002_admin_role_split
        └─ 0006_contact_notes   ← HEAD
```

| Revision | down_revision | 作用 |
|---|---|---|
| `0001_baseline` | `None` | 整个 MAGI schema 一次性建好（dev 模式，见下「collapsed baseline」）。包含：`chat_sessions`、`contacts`、`magics`、`magis`、`settings`、`action_items`（含 `due_date`）、`chat_messages`（含 FTS5 同步触发器）、`memory_entries`（以 `uid` 为键）、`task_presets`、`tasks`（含 `preset_id` / `preset_key` 回指）、`token_usage`、`task_runs`、`mcp_servers`；按需创建 `chat_messages_fts`（FTS5 trigram，依赖 SQLite 编译选项）；并向 `task_presets` 幂等 seed 两个默认模板（每日晨报 / 周回顾）。`downgrade()` 是 no-op，避免误删整个库。 |
| `0002_admin_role_split` | `0001_baseline` | 把 `Contact.role` 拆成两个正交概念：保留 `role`（关系语义，取值收缩为 `assigned` / `contact` / `guest`）并新增独立布尔列 `admin`（WebUI 登录权限）。旧 `role='admin'` 行数据迁移为 `role='assigned', admin=True`。 |
| `0006_contact_notes` | `0002_admin_role_split` | 新建 `contact_notes` 表（一人多备注的一对多结构），并把既有非空的 `contacts.notes` 文本各迁为一行。 |

### collapsed baseline（开发策略）

代码处于 dev 模式（暂无生产升级故事），因此历史上独立的若干迁移已被**吸收进 `0001_baseline`**，不再作为独立 revision 存在：

- 原 `0002_fts5` → FTS5 虚拟表 + 同步触发器，现内联在 baseline 的 `chat_messages` 之后（DDL 取自 `magi/agent/db/migrations._FTS_MIGRATIONS`）。
- 原 `0003_memory_entries_uid` → `memory_entries.employee_id` → `uid` 重命名，现 baseline 直接用 `uid`。
- 原 `0004_action_items_due_date` → `action_items.due_date`，现 baseline 已含该列。
- 原 `0005_mcp_servers` → `mcp_servers` 表，现 baseline 已含该表。

新增 schema 变化的纪律见下文「添加新的 schema 变化」。

## 当前表清单（运行时）

应用表（都在 `Base.metadata`，由 Alembic 或 legacy adoption 创建）：

- `chat_sessions` / `chat_messages`（会话历史；二者另由 `magi.agent.memory.session` 包持有 ORM 模型）
- `contacts`（统一的人表，取代旧的 `contacts` / `contact_entries` / `user_im_bindings`）
- `contact_notes`（由 `0006_contact_notes` 新建）
- `magics`（MAGIC 组织树，自引用 `parent_id`）
- `magis`（MAGI 运行时 agent 行，`magic_position` ∈ {`adam`, `eve`}）
- `settings`（系统级 KV，承载 `system.timezone` 等）
- `action_items` / `token_usage` / `memory_entries`
- `task_presets` / `tasks` / `task_runs`（主动任务调度）
- `mcp_servers`（operator 配置的 MCP server）
- `chat_messages_fts`（FTS5 虚拟表，可选；不在 `Base.metadata`，由 baseline 的 DDL 直接建）
- `alembic_version`（Alembic 维护）
- `meta`（本地 bootstrap KV，由 `local_db.init_sqlite` 用原始 SQL 创建，不属于 `Base.metadata`）

## 启动时行为

`magi.node.run()` → `init_orm(state_dir)`（`magi/agent/db/engine.py`）。`init_orm` 内部：

1. eager-import 所有 model 模块，让表注册到 `Base.metadata`；
2. 若数据库没有 `alembic_version`（legacy C0/C1 库）：
   - `Base.metadata.create_all(engine)` 创建缺失表；
   - `_run_inline_migrations(engine)`（`magi/agent/db/migrations.py`）修复历史表名 / 列 / 索引；
   - `stamp_baseline` 把库 stamp 到 `0001_baseline`；
3. 始终执行 `upgrade_head`（= `alembic command.upgrade head`），把库升到最新 revision；
4. `_seed_default_root` 确保有且仅有一个根 MAGIC（"Genesis"，靠 `parent_id IS NULL` 识别）和一只 `magic_position='adam'` 的 Magi（"Alice"）。

因此容器启动、滚动更新或新 Pod 创建时，数据库会先完成迁移，再启动 WebUI、Telegram
和 scheduler。应用代码启动即自动升级，**不需要在容器里手动调用 Alembic**。

## 旧数据库迁移（adoption）

没有 `alembic_version` 的 C0/C1 数据库走一次性 adoption 流程（见上 `init_orm` 步骤 2）：

1. `Base.metadata.create_all` 创建缺失表；
2. `magi.agent.db.migrations._run_inline_migrations` 修复历史表名、列和索引；
3. 将数据库 stamp 到 `0001_baseline`；
4. 执行后续 Alembic revisions；
5. 后续启动只运行 Alembic，不再运行旧 inline migration。

这个兼容路径只服务于已有数据库。新的 schema 变化**不能**继续添加到
`magi/agent/db/migrations.py`。

## 添加新的 schema 变化

MAGI 采用两种互补的做法：

- **吸收进 baseline（dev 默认）**：纯 DDL 的结构性变化（新表、新列、索引）直接改
  `0001_baseline.py` 的 `upgrade()`，保持「一键建全库」。这正是 `0003`–`0005`
  的历史去向。
- **增量 revision**：当变化需要**数据迁移**（不止 DDL，例如 `0002` 的
  role→role+admin 拆分、`0006` 的 notes 拆分）或需要可回退的独立步骤时，新增一个小
  revision，链在 HEAD 之后。

生成候选 revision：

```bash
uv sync --extra adam --extra eve --extra dev

# 在开发数据库上生成候选 revision
MAGI_STATE_DIR=/tmp/magi-migration-dev \
  alembic -c alembic.ini revision --autogenerate -m "describe schema change"
```

生成后必须人工检查：

- 是否正确处理 SQLite 的 batch alter；
- 是否保留已有数据；
- 是否需要数据迁移而不只是 DDL；
- `upgrade()` 和 `downgrade()` 是否安全（baseline 的 `downgrade()` 是 no-op）；
- 是否需要补充单元测试。

然后在干净数据库和一份旧数据库副本上分别测试：

```bash
MAGI_STATE_DIR=/tmp/magi-migration-test \
  uv run magi --check
```

应用代码启动时会自动执行 revision，不需要在容器里手动调用 Alembic。

## 生产注意事项

- 生产镜像必须包含 `alembic.ini` 和 `magi/agent/db/alembic/`；
- `deploy/Dockerfile` 已将 `alembic.ini` 复制到 `/app/alembic.ini`；
- Alembic 已经是核心 runtime dependency，不再只属于 Adam extra；
- SQLite 默认单副本运行，避免多个 Pod 同时执行 migration 或写入数据库；
- 部署 PostgreSQL / 多副本之前，需要单独审查 migration locking 和应用启动并发策略；
- 不要把真实数据库文件或包含 credentials 的 migration fixture 提交到 Git。

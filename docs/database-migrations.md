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

HEAD = `0001_baseline`（唯一 revision）。

代码处于 dev 模式（暂无生产升级故事），历史上串联的 7 个 follow-on 修订（`0002_admin_role_split`、`0002_drop_contact_provider_api_key`、`0003_add_daily_note_kind`、`0004_rename_magics_to_magic`、`0006_contact_notes`、`0007_eve_runtimes`、`0007_swap_magic_magis_tables`）已被**全部吸收进 `0001_baseline`**，文件本身已删除。所以新数据库一次 `upgrade head` 直接得到最终形态，老数据库若 `alembic_version` 还指向已删除的 revision，启动器会自动 re-stamp 到 `0001_baseline`（见「旧数据库迁移（adoption）」）。

| Revision | down_revision | 作用 |
|---|---|---|
| `0001_baseline` | `None` | 整个 MAGI 最终 schema 一次性建好（dev 模式 collapsed baseline）。包含：`chat_sessions`、`contacts`、`magic`（MAGI Citizen / 单 agent）、`magis`（MAGI Society / 组织树，自引用 `parent_id` + `adam_id` → `magic.id`）、`settings`、`action_items`（含 `due_date` 与 `ux_action_items_open_per_kind` 偏唯一索引）、`chat_messages` + FTS5 同步触发器、`memory_entries`（以 `uid` 为键）、`contact_notes`（含 `kind` / `note_date` 与 `ux_contact_notes_daily` 偏唯一索引）、`eve_runtimes`（`magi_id` → `magic.id`）、`tasks`（含 `preset_id` / `preset_key` 回指）、`task_presets`、`token_usage`、`task_runs`、`mcp_servers`；按需创建 `chat_messages_fts`（FTS5 trigram，依赖 SQLite 编译选项）。 |

### collapsed baseline（开发策略）

代码处于 dev 模式（暂无生产升级故事），因此历史上独立的若干迁移已被**吸收进 `0001_baseline`**，不再作为独立 revision 存在：

- 原 `0002_fts5` → FTS5 虚拟表 + 同步触发器，现内联在 baseline 的 `chat_messages` 之后（DDL 取自 `magi/agent/db/migrations._FTS_MIGRATIONS`）。
- 原 `0002_admin_role_split` → `Contact.role` 拆分 + `admin` 布尔列，现 baseline 直接用最终形态（`role ∈ {assigned, guest}` + `admin` 列）。
- 原 `0002_drop_contact_provider_api_key` → `contacts` 上的 `provider` / `api_key` 删除（凭证现由 `magic` 表持有），现 baseline 已不含这两列。
- 原 `0003_add_daily_note_kind` → `contact_notes.kind` / `note_date` 与 `ux_contact_notes_daily`，现 baseline 已含。
- 原 `0003_memory_entries_uid` → `memory_entries.employee_id` → `uid` 重命名，现 baseline 直接用 `uid`。
- 原 `0004_action_items_due_date` → `action_items.due_date`，现 baseline 已含该列。
- 原 `0004_rename_magics_to_magic` → 兼容极早期开发库中 `magics` 的旧拼写（已并入 swap）。
- 原 `0005_mcp_servers` → `mcp_servers` 表，现 baseline 已含该表。
- 原 `0006_contact_notes` → `contact_notes` 表，现 baseline 已含。
- 原 `0007_eve_runtimes` → `eve_runtimes` 表与索引，现 baseline 已含。
- 原 `0007_swap_magic_magis_tables` → `magic` / `magis` 表命名对调 + EVE runtime 外键目标修正，现 baseline 已直接用最终形态。

新增 schema 变化的纪律见下文「添加新的 schema 变化」。

## 当前表清单（运行时）

应用表（都在 `Base.metadata`，由 Alembic 或 legacy adoption 创建）：

- `chat_sessions` / `chat_messages`（会话历史；二者另由 `magi.agent.memory.session` 包持有 ORM 模型）
- `contacts`（统一的人表，取代旧的 `contacts` / `contact_entries` / `user_im_bindings`，`role ∈ {assigned, guest}`）
- `contact_notes`（一人多备注 + 每日记录，含 `kind` 与 `note_date`）
- `magis`（一群 MAGI 组成的 Agentic Society = MAGIS 组织树，自引用 `parent_id` + `adam_id` → `magic.id`）
- `magic`（MAGIC 运行时 agent 行 = "MAGI Citizens"，以 `magis_id` 归属 Society，持有 `provider` / `api_key`，`magic_position` ∈ {`adam`, `eve`}）
- `settings`（系统级 KV，承载 `system.timezone` 等）
- `action_items` / `token_usage` / `memory_entries`
- `task_presets` / `tasks` / `task_runs`（主动任务调度）
- `eve_runtimes`（EVE 生命周期状态，`magi_id` → `magic.id`）
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
3. 若数据库**有** `alembic_version` 但指向一个 Alembic 已不认识的 revision（典型：以前升级到了 `0007_swap_magic_magis_tables`，本次 rebase 后该文件已删除），`upgrade_head` 入口的 `_rebase_to_canonical_head` 会先 `DELETE FROM alembic_version` 再 stamp 到 `0001_baseline`。DB schema 不动（folded migration 的效果已在 baseline 里），只刷新 bookkeeping 行；
4. 始终执行 `upgrade_head`（= `alembic command.upgrade head`），把库升到最新 revision；
5. `_seed_default_root` 确保有且仅有一个根 MAGI Society（`MAGIS` 行，名为 "Genesis"，靠 `parent_id IS NULL` 识别），随后创建首个 `magic_position='adam'` 的 MAGIC（"Alice"）并绑定为 Genesis 的 Adam。

因此容器启动、滚动更新或新 Pod 创建时，数据库会先完成迁移，再启动 WebUI、Telegram
和 scheduler。应用代码启动即自动升级，**不需要在容器里手动调用 Alembic**。

## 旧数据库迁移（adoption）

没有 `alembic_version` 的 C0/C1 数据库走一次性 adoption 流程（见上 `init_orm` 步骤 2）：

1. `Base.metadata.create_all` 创建缺失表；
2. `magi.agent.db.migrations._run_inline_migrations` 修复历史表名、列和索引；
3. 将数据库 stamp 到 `0001_baseline`；
4. 执行后续 Alembic revisions；
5. 后续启动只运行 Alembic，不再运行旧 inline migration。

针对已升级到 follow-on revision 但本次 rebase 后被删除的情况（典型：旧库
`alembic_version` 行写着 `0007_swap_magic_magis_tables`，该文件已不再存在），
`upgrade_head` 入口会先 re-stamp 到 `0001_baseline` 再跑 `command.upgrade head`，
从而把 bookkeeping 行对齐；DB schema 不动。

这个兼容路径只服务于已有数据库。新的 schema 变化**不能**继续添加到
`magi/agent/db/migrations.py`。

## 添加新的 schema 变化

MAGI 在 dev 模式下只走 collapsed baseline：纯 DDL 的结构性变化（新表、新列、索引）和简单的数据迁移直接改 `0001_baseline.py` 的 `upgrade()`，保持「一键建全库」。引入新的独立 revision 会破坏这一策略，因此不被支持。

新增列 / 表 / 索引的纪律：

1. **DDL** — 直接编辑 `magi/agent/db/alembic/versions/0001_baseline.py` 的 `upgrade()`。SQLite 不可轻易改列的就放 `batch_alter_table` 里。
2. **数据迁移** — 同样在 `upgrade()` 末尾用 `bind.execute(text(...))` 跑一段 `UPDATE` / `INSERT`。代码可以引用 ORM 类（`from magi.agent.db import open_session, MyModel`）。
3. **ORM 模型** — 同步改对应的 `magi/agent/db/models_*.py`（或在 `magi/agent/memory/...` / `magi/agent/proactive/orm_models.py`），保持与 baseline DDL 字面一致。
4. **索引 / 约束** — 同上，要么 inline `unique=True`，要么显式 `create_index(...sqlite_where=...)`。
5. **测试** — 跑 `pytest magi/tests/` 确认 green，特别留意 init_orm / alembic upgrade / 表 CRUD 的路径。

`alembic.ini` 与 `magi/agent/db/alembic/env.py` 不动；历史 follow-on migration 文件
已删除，dev rebaseline 后只剩 `0001_baseline.py`。

## 生产注意事项

- 生产镜像必须包含 `alembic.ini` 和 `magi/agent/db/alembic/`；
- `deploy/Dockerfile` 已将 `alembic.ini` 复制到 `/app/alembic.ini`；
- Alembic 已经是核心 runtime dependency，不再只属于 Adam extra；
- SQLite 默认单副本运行，避免多个 Pod 同时执行 migration 或写入数据库；
- 部署 PostgreSQL / 多副本之前，需要单独审查 migration locking 和应用启动并发策略；
- 不要把真实数据库文件或包含 credentials 的 migration fixture 提交到 Git。
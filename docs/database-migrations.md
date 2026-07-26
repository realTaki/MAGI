# MAGI 数据库迁移

MAGI 的应用数据库由 Alembic 管理，SQLite 的 schema 版本记录在：

```text
alembic_version
```

配置文件：

```text
alembic.ini
```

迁移脚本：

```text
magi/agent/db/alembic/versions/
```

## 启动时行为

`magi.node.run()` 初始化 ORM 时会自动执行：

```text
alembic upgrade head
```

因此容器启动、滚动更新或新 Pod 创建时，数据库会先完成迁移，再启动 WebUI、Telegram
和 scheduler。

当前版本：

```text
0001_baseline
0002_fts5
0003_memory_entries_uid
0004_action_items_due_date
```

## 旧数据库迁移

没有 `alembic_version` 的 C0/C1 数据库会进入一次性 adoption 流程：

1. 使用旧的 `magi.agent.db.migrations` 修复历史表名、列和索引；
2. 将数据库 stamp 到 `0001_baseline`；
3. 执行后续 Alembic revisions；
4. 后续启动只运行 Alembic，不再运行旧 inline migration。

这个兼容路径只服务于已有数据库。新的 schema 变化不能继续添加到
`magi/agent/db/migrations.py`。

## 添加新的 schema 变化

每次修改 ORM model 后，必须创建一个新的 revision：

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
- `upgrade()` 和 `downgrade()` 是否安全；
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

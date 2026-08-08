# Proactive 模块重构设计

> 状态：设计文档，尚未实现  
> 日期：2026-08-08

## 1. 目标

将 `magi/proactive` 从"被动的纯策略函数"升级为"主动 Worker"，使其：

1. **作为 Worker 运行** — 最后拉起，确保整个 Agent 正常后再开始处理
2. **直接操作 bus** — 不再通过间接调用方（onboarding API、contacts API）来间接触发，而是自己订阅 Job 并执行
3. **启动时引导检查** — 对已存在的 admin 发 ActionItem 提醒设置 provider（仅 Adam 的 MAGI）
4. **幂等性** — 提醒不会重复创建，已完成的不再提醒
5. **订阅 seedPresetTasksJob** — API/Tool 操作新增 assigned/admin 时，通过 bus 发布 Job，proactive Worker 领取并执行

---

## 2. 架构变更总览

### 2.1 现状

```
onboarding API (/complete)
  └─> ensure_for_admin(book, admin_id)          # 同步调用，在 API 请求线程内执行
        └─> book.add()

contacts API (create_contact / update_contact)
  └─> bus.seed_presets_for_contact(contact_id)   # 同步调用，在 API 请求线程内执行
        ├─> plan_presets_for_contact()            # 纯策略
        └─> INSERT Task 行
```

### 2.2 目标

```
Bootstrap
  └─> ProactiveWorker.start()
        ├─> 检查自己是不是所处 MAGIS 的 Adam
        ├─> 如果是 Adam → 对已有 admin 幂等插入 credentials nudge
        └─> spawn _run() loop

API/Tool (create_contact / update_contact / onboarding)
  └─> bus.seed_preset_tasks_job_board.publish(SeedPresetTasksJob(...))
        # 异步，不阻塞 API 响应

ProactiveWorker._run() loop
  ├─> claim SeedPresetTasksJob
  ├─> plan_presets_for_contact()  # 纯策略函数（保留）
  ├─> 幂等检查 + INSERT Task 行
  └─> claim CredentialsNudgeJob（如果需要的话）
```

### 2.3 关键决策

| 决策 | 说明 |
|---|---|
| 最后拉起 | startup order: provider → tool → agent → delivery → **proactive** |
| 直接操作 bus | Worker 构造接受 `NewBus`，通过 `bus.contacts_book`、`bus.action_items_book`、`bus.tasks_book` 直接读写 |
| 保留纯策略函数 | `plan_presets_for_contact()` 保持纯函数不变；`credentials_nudge.py` 的核心逻辑也保持纯函数 |
| 新增 Job Board | `seedPresetTasksJobBoard` — 一个标准 `BaseJobBoard`，用于异步投递种子任务请求 |

---

## 3. 新增 Job Board: `seedPresetTasksJobBoard`

### 3.1 设计

遵循现有 `new_bus/guild/` 下的模式（如 `runToolJob.py`、`callLLMJob.py`）。

**Job DTO**：

```python
@dataclass(frozen=True, slots=True)
class SeedPresetTasksJob:
    job_id: str        # 自动生成
    contact_id: int    # 目标联系人
    trigger: str       # "contact_created" | "contact_promoted" | "bootstrap"
    status: str = "pending"
    attempts: int = 0
    ...
```

**Result DTO**：

```python
@dataclass(frozen=True, slots=True)
class SeedPresetTasksResult:
    job_id: str
    success: bool
    inserted: int = 0      # 实际插入的 Task 行数
    skipped: int = 0       # 跳过的 preset 数
    error: str | None = None
```

**ORM 行**：新建 `_SeedPresetTasksJobRow`，表名 `seed_preset_tasks_jobs`，包含标准 Job 列（`job_id`, `status`, `attempts`, `leased_until`, `started_at`, `completed_at`）+ payload 列（`contact_id`, `trigger`）+ result 列（`inserted`, `skipped`, `error`）。

**Board**：

```python
class seedPresetTasksJobBoard(BaseJobBoard[_SeedPresetTasksJobRow, SeedPresetTasksJob, SeedPresetTasksResult]):
    job_model = _SeedPresetTasksJobRow
    job_cls = SeedPresetTasksJob
    result_cls = SeedPresetTasksResult

    def publish(self, job: SeedPresetTasksJob) -> str:
        # 写入 ORM 行，返回 job_id
```

### 3.2 文件位置

```
magi/new_bus/guild/seedPresetTasksJob.py
```

在 `magi/new_bus/guild/__init__.py` 和 `magi/new_bus/bootstrap.py` 中注册。

---

## 4. ProactiveWorker

### 4.1 设计

参照 `ToolsWorker` 和 `ProvidersWorker` 的模式：

```python
class ProactiveWorker:
    def __init__(self, bus: NewBus, *, poll_seconds: float = 0.25):
        self.bus = bus
        self.poll_seconds = poll_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    async def start(self) -> None:
        # 1. 引导检查：如果本 MAGI 是所处 MAGIS 的 Adam，对已有 admin 插入 credentials nudge
        await self._bootstrap()

        # 2. 启动主循环
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="magi-proactive-worker")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while not self._stopping:
            # 1. Claim SeedPresetTasksJob
            job = await asyncio.to_thread(
                self.bus.seed_preset_tasks_job_board.claim
            )
            if job is not None:
                await self._handle_seed_job(job)
                continue

            # 2. 未来可扩展更多 Job 类型
            # ...

            await asyncio.sleep(self.poll_seconds)
```

### 4.2 `_bootstrap()` — 启动时引导检查

```python
async def _bootstrap(self) -> None:
    """启动时：如果本 MAGI 是 Adam，对已有 admin 幂等插入 credentials nudge。"""
    # 1. 解析本 MAGI 的 MAGIS 上下文
    magis_id = self._resolve_magis_id()
    if magis_id is None:
        return  # 没有 MAGIS DB，跳过

    # 2. 判断本 MAGI 是不是该 MAGIS 的 Adam
    if not self._is_adam(magis_id):
        logger.info("proactive worker: not Adam, skipping bootstrap")
        return

    # 3. 获取该 MAGIS 的所有 admin（MAGIS 级别，来自 magis_admins 表）
    magis_admins_book = getattr(self.bus, "magis_admins_book", None)
    if magis_admins_book is None:
        return
    admin_rows = magis_admins_book.list_for_magis(magis_id=magis_id)
    if not admin_rows:
        logger.info("proactive worker: no admins for magis_id=%d, skipping", magis_id)
        return

    # 4. 对每个 admin 幂等插入 nudge
    spec = CREDENTIALS_NUDGE
    for entry in admin_rows:
        uid = entry.uid  # contacts.id
        existing = [
            row for row in self.bus.action_items_book.list_actions(
                owner_uid=uid,
                include_completed=False,
                source=SOURCE_PROACTIVE,
            )
            if row.title == spec.title
        ]
        if existing:
            continue  # 已有 open nudge，跳过
        # 额外检查：是否已完成（include_completed=True）
        completed = [
            row for row in self.bus.action_items_book.list_actions(
                owner_uid=uid,
                include_completed=True,
                source=SOURCE_PROACTIVE,
            )
            if row.title == spec.title and row.completed_at is not None
        ]
        if completed:
            continue  # 已完成，不再提醒
        # 插入
        self.bus.action_items_book.add(
            uid=uid,
            title=spec.title,
            description=spec.description,
            target_url=spec.target_url,
            source=SOURCE_PROACTIVE,
        )
        logger.info("proactive worker: bootstrap for admin uid=%d", uid)
```

### 4.3 `_resolve_magis_id()` + `_is_adam()` — MAGIS 身份判断

```python
def _resolve_magis_id(self) -> int | None:
    """解析本 MAGI 所属的 MAGIS id。

    通过 memberships_book.get(magi_id) → membership.magis_id。
    返回 None 表示没有 MAGIS DB 或本 MAGI 未注册于任何 MAGIS。
    """
    magi_id = _read_magi_id()  # 从 runtime.json 读取
    if magi_id is None:
        return None
    memberships_book = getattr(self.bus, "memberships_book", None)
    if memberships_book is None:
        return None
    membership = memberships_book.get(magi_id=magi_id)
    if membership is None:
        return None
    return membership.magis_id

def _is_adam(self, magis_id: int) -> bool:
    """判断本 MAGI 是否是给定 MAGIS 的 Adam。"""
    magi_id = _read_magi_id()
    if magi_id is None:
        return False
    magis_book = getattr(self.bus, "magis_book", None)
    if magis_book is None:
        return False
    magis_node = magis_book.get(magis_id=magis_id)
    if magis_node is None:
        return False
    return magis_node.adam_id == magi_id
```

### 4.4 `_handle_seed_job()` — 处理种子任务

```python
async def _handle_seed_job(self, job: SeedPresetTasksJob) -> None:
    """处理 SeedPresetTasksJob：执行预设任务播种。"""
    try:
        # 复用现有的纯策略函数 plan_presets_for_contact()
        # 区别：现在在 Worker 内执行，直接操作 bus 的 Books

        # 1. 读取 contact
        contact = self.bus.contacts_book.get(contact_id=job.contact_id)
        if contact is None:
            self._submit_seed_failure(job, f"contact {job.contact_id} not found")
            return

        contact_snapshot = ContactSnapshot(
            id=contact.id,
            name=contact.name,
            display_name=contact.display_name,
            role=contact.role,
        )

        # 2. 读取 TaskPresets（通过 TaskBook 或专门的 Preset 读取方式）
        # TODO: 需要确认 new_bus 是否有 TaskPreset 的 Book
        presets = self._load_presets()

        # 3. 调用纯策略函数
        plan = plan_presets_for_contact(
            contact_snapshot,
            presets,
            system_timezone=self._system_timezone(),
        )

        # 4. 幂等插入 Task 行
        inserted = 0
        skipped = 0
        for seed in plan.seeds:
            # 检查 uid + preset_id 是否已有
            existing = self._task_exists(contact_id=job.contact_id, preset_id=seed.preset_id)
            if existing:
                skipped += 1
                continue
            # 插入 Task 行
            self._insert_task(seed, contact_id=job.contact_id)
            inserted += 1

        skipped += len(plan.skipped)

        # 5. 提交结果
        result = SeedPresetTasksResult(
            job_id=job.job_id,
            success=True,
            inserted=inserted,
            skipped=skipped,
        )
        self.bus.seed_preset_tasks_job_board.submit_result(key=job.job_id, result=result)

    except Exception as exc:
        logger.exception("proactive worker: seed job %s failed", job.job_id)
        self._submit_seed_failure(job, str(exc))
```

---

## 5. 模块结构变更

### 5.1 `magi/proactive/` 文件变化

| 文件 | 变更 |
|---|---|
| `__init__.py` | 新增导出 `ProactiveWorker`, `start_proactive_worker`, `stop_proactive_worker` |
| `contracts.py` | 不变 |
| `credentials_nudge.py` | **简化**：移除 `ensure_for_admin()` 对 `ActionItemBook` 的直接依赖，保留纯逻辑部分（spec 常量 + 幂等判断逻辑），改为接受 books 作为参数或在 Worker 内组合 |
| `task_presets.py` | 不变（纯策略函数保留） |
| `worker.py` | **新增**：`ProactiveWorker` 类 |

### 5.2 `magi/new_bus/guild/` 新增

| 文件 | 说明 |
|---|---|
| `seedPresetTasksJob.py` | `SeedPresetTasksJob`, `SeedPresetTasksResult`, `seedPresetTasksJobBoard` |

### 5.3 `magi/new_bus/bootstrap.py` 变更

- 新增 `seed_preset_tasks_job_board` 字段到 `NewBus`
- 在 `_bootstrap_with_dirs` 中实例化并注入

### 5.4 `magi/startup/runtime.py` 变更

Worker 启动顺序调整：

```python
await start_provider_worker(bus=new_bus)      # 1st
await start_tool_worker(bus=new_bus)          # 2nd
await start_agent_worker()                    # 3rd
await start_delivery_worker()                 # 4th
await start_proactive_worker(bus=new_bus)     # 5th (最后)
```

关闭顺序相应调整（proactive 先停）。

同样更新 `worker_lifespan()`。

---

## 6. API 层改造说明

> **本阶段仅添加注释，不实际实现。** API 层的实际改造将在后续阶段完成。

### 6.1 `magi/channels/api/contacts.py`

**`create_contact`**（约 line 347-354）：

```python
# TODO(proactive-refactor): 改为发布 SeedPresetTasksJob 到 bus，
# 由 ProactiveWorker 异步消费。当前同步调用 seed_presets_for_contact
# 的方式将在 Worker 就绪后移除。
#
# 改后代码大致为：
#   if view.role == "assigned":
#       bus.seed_preset_tasks_job_board.publish(SeedPresetTasksJob(
#           job_id=new_job_id(),
#           contact_id=view.id,
#           trigger="contact_created",
#       ))
#
if view.role == "assigned":
    try:
        bus.seed_presets_for_contact(view.id)
    except Exception as exc:
        logger.warning(...)
```

**`update_contact`**（约 line 501-516）：

```python
# TODO(proactive-refactor): 同上，改为发布 SeedPresetTasksJob。
# trigger="contact_promoted"
#
if newly_assigned:
    try:
        bus.seed_presets_for_contact(view.id)
    except Exception as exc:
        logger.warning(...)
```

### 6.2 `magi/channels/api/onboarding.py`

**`complete_onboarding`**（约 line 393-410）：

```python
# TODO(proactive-refactor): credentials nudge 的插入已移至
# ProactiveWorker._bootstrap()。但 onboarding 完成时仍需
# 确保当前 session 的管理员立刻收到提醒（不等 Worker 下次轮询）。
# 方案：
#   A) 发布一个 CredentialsNudgeJob 让 Worker 处理
#   B) 直接调用 ensure_for_admin()（保留同步路径作为兜底）
#   C) Worker 在启动时已处理过 bootstrap 情况，此处可省略
#
# 当前保留同步调用，待 Worker 就绪后评估是否需要改为异步。
#
try:
    admins = bus.contacts.list_admins()
    inserted = sum(
        ensure_credentials_nudge(book=bus.action_items_book, admin_id=admin.id)
        for admin in admins
    )
except Exception:
    ...
```

---

## 7. 幂等性策略

### 7.1 Credentials Nudge

三层幂等：

| 检查 | 方式 |
|---|---|
| 已有 open nudge（同一 title） | `list_actions(include_completed=False, source=SOURCE_PROACTIVE)` + title 匹配 |
| 已完成（completed_at 不为 None） | `list_actions(include_completed=True, source=SOURCE_PROACTIVE)` + title 匹配 + `completed_at is not None` |
| 启动时重复调用 | Worker.start() 只在进程启动时执行一次；`start_proactive_worker` 使用模块级单例保证 |

### 7.2 Seed Preset Tasks

两层幂等：

| 检查 | 方式 |
|---|---|
| `uid + preset_id` 已有 Task 行 | Worker 在 INSERT 前检查 `tasks` 表 |
| Job 重复投递 | Job Board 的 claim 机制保证每条 Job 只被一个 Worker 消费 |

---

## 8. 风险与注意事项

1. **Adam 判断依赖 MAGIS DB**：当 `magis_book` 为 None（没有 MAGIS DB）时，`_resolve_magis_id()` 返回 None，跳过 bootstrap。单机模式没有 MAGIS 概念，行为正确。

2. **`seed_presets_for_contact` 当前在 bus.task service 中**：该方法目前实现在 `magi/bus/jobs/services/task.py` 中，操作的是旧的 ORM 模型。Worker 化后，需要在 ProactiveWorker 中直接操作 new_bus 的 `tasks_book`。但当前 `TaskBook` 可能还不支持 preset 相关字段（`preset_id`, `preset_key`）。需要先确认/补齐 TaskBook 的 schema。

3. **启动顺序**：proactive Worker 最后启动，依赖 contacts_book、action_items_book、tasks_book、magis_book 等均已就绪。这些 Book 在 `bootstrap_new_bus()` 中全部创建完毕后才传入 Worker，所以顺序安全。

4. **Credentials nudge 的 `ensure_for_admin()` 迁移**：该函数当前接受 `ActionItemBook` 参数并直接调用 `book.add()`。Worker 化后逻辑内嵌到 `ProactiveWorker._bootstrap()` 中，原函数可以保留作为纯工具函数或废弃。

5. **TaskPreset 的读取**：`plan_presets_for_contact()` 接受 `list[TaskPresetSnapshot]` 作为参数。目前 TaskPreset 表在旧 bus 中（`magi/bus/db/models/local/task_preset.py`），new_bus 的 `TaskBook` 可能尚未包含 preset 相关方法。Worker 需要能从 new_bus 读取 TaskPreset 或保持从旧 bus 读取的兼容路径。

---

## 9. 文件变更清单

### 新增文件

| 文件 | 说明 |
|---|---|
| `magi/proactive/worker.py` | ProactiveWorker 类 + start/stop 单例 |
| `magi/new_bus/guild/seedPresetTasksJob.py` | Job Board |

### 修改文件

| 文件 | 变更内容 |
|---|---|
| `magi/proactive/__init__.py` | 新增 Worker 相关导出 |
| `magi/proactive/credentials_nudge.py` | 简化/重构 `ensure_for_admin`（可选，取决于是否保留同步路径） |
| `magi/new_bus/guild/__init__.py` | 导出 SeedPresetTasksJob 相关 |
| `magi/new_bus/bootstrap.py` | 新增 `seed_preset_tasks_job_board` 到 NewBus |
| `magi/startup/runtime.py` | 调整启动顺序，新增 proactive worker |
| `magi/channels/api/contacts.py` | 添加 TODO 注释（本阶段不实现） |
| `magi/channels/api/onboarding.py` | 添加 TODO 注释（本阶段不实现） |

---

## 10. 实现阶段

| 阶段 | 内容 |
|---|---|
| Phase 1 | 新增 `seedPresetTasksJobBoard` + 注册到 NewBus |
| Phase 2 | 实现 `ProactiveWorker`（含 `_bootstrap` + `_handle_seed_job`） |
| Phase 3 | 调整 `runtime.py` 启动顺序 |
| Phase 4 | 简化 `credentials_nudge.py`，将 `ensure_for_admin` 逻辑迁移到 Worker |
| Phase 5 | API 层改为发布 Job（替换同步调用） |
| Phase 6 | 清理旧 bus 中 `seed_presets_for_contact` 的 TaskService 方法 |

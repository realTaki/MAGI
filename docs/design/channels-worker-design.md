# Channels Worker 设计书

> 版本: v1.0 | 日期: 2026-08-08 | 状态: Draft

## 目录

1. [背景与动机](#1-背景与动机)
2. [现状分析](#2-现状分析)
3. [架构决策](#3-架构决策)
4. [整体架构](#4-整体架构)
5. [ChannelWorker 基类](#5-channelworker-基类)
6. [TelegramWorker](#6-telegramworker)
7. [TaskWorker](#7-taskworker)
8. [WebUIWorker](#8-webuiworker)
9. [A2AWorker](#9-a2aworker)
10. [DeliveryWorker 废弃计划](#10-deliveryworker-废弃计划)
11. [启动与组合根](#11-启动与组合根)
12. [数据流总览](#12-数据流总览)
13. [迁移路径](#13-迁移路径)
14. [风险与注意事项](#14-风险与注意事项)

---

## 1. 背景与动机

### 1.1 现状问题

当前 `magi/channels/` 模块存在三个结构性问题：

**生命周期异构。** Telegram 跑在守护线程里（独立 asyncio event loop），Task 跑在 APScheduler 线程里（又一个独立 event loop），A2A/WebUI 入站挂在 FastAPI 路由上，而出站统一由 `DeliveryWorker` 处理。四种通道，四种启动方式，四种关闭方式，没有统一的抽象。

**旧总线依赖。** 所有通道代码通过 `get_bus()` 全局单例访问 `magi.bus.Bus`（旧总线），而 ProvidersWorker、ToolsWorker、AgentWorker、ProactiveWorker 等核心 worker 已经全部迁移到 `new_bus` 构造器注入模式。两个总线并存，通道模块孤悬在旧架构里。

**入站/出站职责分裂。** 入站（接收外部输入）由各通道自己管，出站（Agent 输出投递）由统一的 `DeliveryWorker` 管。一个通道的完整行为被拆在两个地方——入站代码在 `channels/telegram/bot.py`，出站代码在 `channels/delivery.py` 的 `if claim.channel == "tg"` 分支里。

### 1.2 目标

1. **统一 Worker 模式。** 每个通道都有自己的 worker 类，遵循与 `ToolsWorker`/`ProvidersWorker` 相同的构造器注入 `NewBus` 模式、相同的 `start()`/`stop()` 生命周期。
2. **入站出站归位。** 每个 Channel Worker 同时负责该通道的入站（接收外部输入→发布 ChatJob）和出站（认领 DeliveryJob→投递到外部），消除 `DeliveryWorker` 的 `if channel ==` 分支路由。
3. **独立生命周期。** 启用/禁用某个通道只需启动/停止对应 worker，互不影响。

---

## 2. 现状分析

### 2.1 现有通道一览

| 通道 | 入站机制 | 出站机制 | 生命周期 | 总线 |
|------|---------|---------|---------|------|
| **Telegram** | 守护线程，长轮询 TG API，`_on_message` → 解析联系人 → 追加 Session → `publish_input(AgentMessage)` | `DeliveryWorker` 的 `channel=="tg"` 分支 → 原始 HTTP 发送 | `start_bot()` / `stop_bot()` | `get_bus()` |
| **Task** | APScheduler 线程，cron/run_at 触发 → `execute_task()` → 构建 context prompt → 追加 Session → `publish_input(AgentMessage)` | 无（任务触发后由 Agent 的工具调用 `send_message` 驱动） | `start_scheduler()` / `stop_scheduler()` | `get_bus()` |
| **A2A** | FastAPI `POST /a2a/inbox` 路由 → HMAC 验证 → `publish_input(AgentMessage)` | `DeliveryWorker` 的 `channel=="a2a"` 分支 → HTTP POST | FastAPI 应用生命周期 | `get_bus()` |
| **WebUI** | FastAPI HTTP 路由（chat/settings/contacts...）→ 类似 `publish_input` | `DeliveryWorker` 的 `channel=="webui"` 分支 → 追加 Session 消息 | uvicorn 生命周期 | `get_bus()` |

### 2.2 DeliveryWorker 现状

```python
# magi/channels/delivery.py — 当前代码摘录
class DeliveryWorker:
    def __init__(self, *, poll_seconds=0.25):
        self.bus = get_bus()  # ⚠️ 旧总线
        ...

    async def _run(self):
        while not self._stopping:
            claim = self.bus.delivery.claim_next(self.worker_id)
            if claim is None:
                await asyncio.sleep(self.poll_seconds)
                continue
            await self._send(claim)

    async def _send(self, claim):
        # ⚠️ 三个 if-elif 分支路由
        if claim.channel == "tg":       ... # TG 发送
        elif claim.channel == "a2a":    ... # A2A HTTP
        elif claim.channel == "webui":  ... # Session 追加
```

### 2.3 现有 Worker 模式（基准）

```python
# magi/tools/worker.py — 新 worker 模式的标准形态
class ToolsWorker:
    def __init__(self, bus: NewBus, *, poll_seconds=0.25, concurrency=None):
        self.bus = bus                           # 构造器注入
        self._task: asyncio.Task | None = None
        self._stopping = False

    async def start(self):
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._stopping = True
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task

    async def _run(self):
        while not self._stopping:
            job = await asyncio.to_thread(self.bus.tool_job_board.claim)
            if job is None:
                await asyncio.sleep(self.poll_seconds)
                continue
            await self._execute(job)
```

---

## 3. 架构决策

### 决策 1：Channel → Agent 的通信方向

```
Channel → Agent:     ChatJob           (发布到 agent_job_board)
Agent  → Channel:    DeliveryJob       (发布到 delivery_job_board)
```

**不用 `chatJobResult` 携带回复内容的理由：**

- **一对多天然支持。** 一个 ChatJob 可能产生 N 条输出（中间状态、工具结果、最终回复），DeliveryJob 支持 Agent 在 turn 中途就 publish 流式投递，Channel 不必等 ChatJob 完成。
- **生命周期解耦。** ChatJob 还在 `processing` 时 DeliveryJob 已开始投递，支持流式输出、"正在输入..."等交互反馈。
- **职责清晰。** ChatJob = "Agent 的调度单元"，DeliveryJob = "要给用户发的内容"。前者存元数据（token 用量、工具调用摘要），后者存业务内容。
- **Board 消费模式一致。** 每个 worker 消费一个 job board。Channel Worker 消费 delivery_job_board，AgentWorker 消费 agent_job_board，对称干净。

### 决策 2：每个 Channel 一个 Worker

**不采用统一 `ChannelsWorker` 的理由：**

- 轮询/触发机制根本不同：TG 是长连接长轮询，Task 是 cron 定时，WebUI/A2A 是 HTTP 被动接收
- 故障隔离：TG 连接断开不应导致 Task 调度也停摆
- 并发模型冲突：TG 需自己的 event loop（python-telegram-bot），Task 需 cron 精度，强行合并反而增加复杂度
- 符合开闭原则：加新通道 = 加新 worker 类，不改现有代码
- 与现有 worker 架构一致（都通过模块级 `start_xxx_worker(bus=new_bus)` 启动）

### 决策 3：入站出站合一

每个 Channel Worker 同时负责该通道的**入站**（接收→发布 ChatJob）和**出站**（认领 DeliveryJob→投递），将当前分散在 `bot.py`/`scheduler.py`（入站）和 `delivery.py`（出站）的逻辑收归一处。

**例外：** WebUI/API 的入站是 FastAPI HTTP 路由，这部分保持为 HTTP handler，不变成 worker 的轮询逻辑。因此 `WebUIWorker` 是唯一"只管出不管入"的 worker。

---

## 4. 整体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                        NewBus                                     │
│                                                                   │
│  agent_job_board          delivery_job_board                      │
│  (ChatJob publish/claim)  (DeliveryJob publish/claim)             │
│       ▲                         │                                 │
│       │ publish                 │ claim                           │
│       │                         ▼                                 │
│  ┌────┴──────────┐    ┌─────────────────────────────┐            │
│  │ Channel Worker │    │      Channel Worker          │            │
│  │  (入站方向)    │    │       (出站方向)             │            │
│  │                │    │                              │            │
│  │ TelegramWorker │    │ TelegramWorker ──▶ TG API    │            │
│  │ TaskWorker     │    │ WebUIWorker    ──▶ Session   │            │
│  │ (A2A 路由)    │    │ A2AWorker      ──▶ HTTP POST │            │
│  └───────────────┘    └──────────────────────────────┘            │
│                                                                   │
│  agent_job_board ←── AgentWorker  处理 ChatJob → publish 结果     │
│       │                     │                                     │
│       │ claim               │ 产生 DeliveryJob                    │
│       ▼                     ▼                                     │
│  AgentWorker ──────▶ delivery_job_board                           │
└──────────────────────────────────────────────────────────────────┘
```

**Worker 全景：**

| Worker | 消费的 Board | 角色 |
|--------|-------------|------|
| `TelegramWorker` | 入站: 长轮询 TG → publish ChatJob; 出站: claim delivery(channel=tg) → HTTP 发送 | 自分发 |
| `TaskWorker` | 入站: 轮询 task Book → publish ChatJob | 纯入站 |
| `WebUIWorker` | 出站: claim delivery(channel=webui) → Session 追加 | 纯出站 |
| `A2AWorker` | 出站: claim delivery(channel=a2a) → HTTP POST | 纯出站 |
| `AgentWorker` | claim ChatJob → 处理 → publish DeliveryJob(s) | Agent |
| `DeliveryWorker` | → 逐步废弃 | 废弃中 |

---

## 5. ChannelWorker 基类

```python
# magi/channels/workers/base.py

from __future__ import annotations

import asyncio
import logging
import uuid
from abc import ABC, abstractmethod
from contextlib import suppress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magi.new_bus import NewBus

logger = logging.getLogger("magi.channels.worker")


class ChannelWorker(ABC):
    """所有 Channel Worker 的基类。

    遵循 new_bus 构造器注入模式，与 :class:`magi.tools.worker.ToolsWorker`、
    :class:`magi.providers.worker.ProvidersWorker` 对齐。

    子类只需实现 :meth:`_run`，在其中定义自己的入站/出站轮询逻辑。
    """

    @property
    @abstractmethod
    def channel_name(self) -> str:
        """通道标识，如 ``"tg"``、``"task"``、``"webui"``、``"a2a"``。"""
        ...

    def __init__(
        self,
        bus: NewBus,
        *,
        poll_seconds: float = 0.25,
    ) -> None:
        self.bus = bus
        self.poll_seconds = poll_seconds
        self.worker_id = f"{self.channel_name}-{uuid.uuid4().hex[:8]}"
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    async def start(self) -> None:
        """启动 worker 的轮询循环。幂等。"""
        if self._task is not None:
            return
        self._stopping = False
        self._task = asyncio.create_task(
            self._run(), name=f"magi-channel-{self.channel_name}"
        )
        logger.info("channel worker %s started", self.channel_name)

    async def stop(self) -> None:
        """停止 worker，取消轮询任务。幂等。"""
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("channel worker %s stopped", self.channel_name)

    @abstractmethod
    async def _run(self) -> None:
        """子类实现：定义自己的轮询循环。

        典型模式：
        - 入站 worker：轮询外部源 → publish ChatJob
        - 出站 worker：claim DeliveryJob → 投递 → submit_result
        - 混合 worker：两个协程并发跑
        """
        ...
```

---

## 6. TelegramWorker

```python
# magi/channels/workers/telegram.py

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from magi.channels.workers.base import ChannelWorker

if TYPE_CHECKING:
    from magi.new_bus import NewBus
    from magi.new_bus.guild.chatJob import ChatJob
    from magi.new_bus.guild.deliveryJob import DeliveryJob

logger = logging.getLogger("magi.channels.telegram.worker")


class TelegramWorker(ChannelWorker):
    """Telegram 通道 Worker：入站长轮询 + 出站投递。

    入站：维护 python-telegram-bot Application，收到消息 →
         解析联系人/创建 Session（通过 new_bus Books）→
         发布 ChatJob 到 agent_job_board。

    出站：从 delivery_job_board 认领 channel=="tg" 的 Job →
         通过原始 HTTP 发送到 TG → submit_result。

    两个方向在 :meth:`_run` 中用 ``asyncio.gather`` 并发运行。
    """

    channel_name = "tg"

    def __init__(
        self,
        bus: NewBus,
        *,
        poll_seconds: float = 0.25,
        delivery_poll_seconds: float = 0.1,
    ) -> None:
        super().__init__(bus, poll_seconds=poll_seconds)
        self._delivery_poll_seconds = delivery_poll_seconds
        self._bot_app: object | None = None  # Application
        self._shutdown_event: asyncio.Event | None = None

    async def start(self) -> None:
        # 检查 bot token 配置
        bot_token = self.bus.settings_book.get("telegram.bot_token")
        if not bot_token:
            logger.info("TelegramWorker: no bot_token configured; skipping start")
            return
        await super().start()

    async def _run(self) -> None:
        """并发运行入站（TG 长轮询）和出站（delivery 消费）。"""
        await asyncio.gather(
            self._run_inbound(),
            self._run_outbound(),
        )

    # ── 入站 ────────────────────────────────────────────────────────

    async def _run_inbound(self) -> None:
        """启动 python-telegram-bot Application，长轮询消息。

        替代当前 `channels/telegram/bot.py:_run_forever` 的守护线程方案。
        Worker 跑在主 event loop 里，不需要额外的线程。
        """
        from telegram.ext import Application, MessageHandler, filters

        token = self.bus.settings_book.get("telegram.bot_token")
        if not token:
            return

        app = (
            Application.builder()
            .token(str(token))
            .concurrent_updates(True)
            .connect_timeout(15)
            .read_timeout(15)
            .write_timeout(15)
            .pool_timeout(5)
            .build()
        )
        app.add_handler(MessageHandler(filters.ALL, self._on_tg_message))
        self._bot_app = app
        self._shutdown_event = asyncio.Event()

        try:
            await app.initialize()
            await app.start()
            await app.updater.start_polling(poll_interval=1.0, timeout=10)
            await self._shutdown_event.wait()
        except RuntimeError as exc:
            logger.warning("TelegramWorker inbound: %s", exc)
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
            self._bot_app = None

    async def _on_tg_message(self, update, context) -> None:
        """TG 消息处理器 — 对齐当前 `bot.py:_on_message` 的逻辑。

        核心变化：不再调用 `get_bus()`，改用 `self.bus` (NewBus)：
        - 联系人解析 → `self.bus.contacts_book`
        - Session 管理 → `self.bus.sessions_book` / `self.bus.messages_book`
        - 发布 Agent 处理 → `self.bus.agent_job_board.publish(ChatJob(...))`
        """
        if update.effective_chat is None or update.effective_message is None:
            return
        tgid = str(update.effective_chat.id)
        text = update.effective_message.text or ""

        # 1. 解析联系人
        contact = self._resolve_contact(tgid)
        if contact is None or contact.role not in ("assigned", "admin"):
            # 陌生人 / guest → 送发现 tgid 的回执
            await self._send_stranger_reply(update, tgid, contact)
            return

        if not text.strip():
            await update.effective_message.reply_text("MAGI 目前只处理文字消息。")
            return

        # 2. Session 管理（"一个 TG chat 一个 session forever"）
        session_id = self._resolve_tg_session(uid=contact.id, tgid=tgid)

        # 3. 追加用户消息到 session
        self.bus.messages_book.append(
            uid=contact.id,
            session_id=session_id,
            role="user",
            text=text,
        )

        # 4. 读回执
        await self._send_read_receipt(update)

        # 5. 发布 ChatJob 到 Agent
        await self.bus.agent_job_board.publish(ChatJob(
            external_id=f"telegram:{tgid}:{update.effective_message.message_id}",
            source_type="tg",
            text=text,
            channel="tg",
            session_id=session_id,
            uid=contact.id,
            caller_role=contact.role,
            metadata={
                "tg_chat_id": tgid,
                "tg_message_id": update.effective_message.message_id,
            },
        ))

    # ── 出站 ────────────────────────────────────────────────────────

    async def _run_outbound(self) -> None:
        """认领 channel=="tg" 的 DeliveryJob → HTTP 发送 → submit_result。

        替代当前 `channels/delivery.py` 中 `claim.channel == "tg"` 分支。
        """
        bot_token = self.bus.settings_book.get("telegram.bot_token")
        if not bot_token:
            logger.warning("TelegramWorker: no bot_token; delivery disabled")
            return

        while not self._stopping:
            try:
                job = await asyncio.to_thread(
                    self.bus.delivery_job_board.claim, channel="tg"
                )
            except Exception:
                logger.exception("TelegramWorker: delivery claim failed")
                await asyncio.sleep(self._delivery_poll_seconds)
                continue

            if job is None:
                await asyncio.sleep(self._delivery_poll_seconds)
                continue

            try:
                await self._deliver_tg(job, str(bot_token))
            except Exception:
                logger.exception("TelegramWorker: delivery %s failed", job.job_id)
                self.bus.delivery_job_board.submit_result(
                    key=job.job_id,
                    result=DeliveryResult(job_id=job.job_id, success=False, error="send failed"),
                )
            else:
                self.bus.delivery_job_board.submit_result(
                    key=job.job_id,
                    result=DeliveryResult(job_id=job.job_id, success=True),
                )

    async def _deliver_tg(self, job: DeliveryJob, bot_token: str) -> None:
        """原始 HTTP 发送到 TG。"""
        chat_id = int(job.destination)
        text = str(job.payload.get("text") or "")
        # 复用现有 _send_via_raw_http 的逻辑，但参数来自 self.bus
        await self._send_via_raw_http(bot_token, chat_id, text)

    # ── helpers ──────────────────────────────────────────────────────

    async def stop(self) -> None:
        """额外处理：通知 TG Application 停止轮询。"""
        if self._shutdown_event is not None:
            self._shutdown_event.set()
        await super().stop()
```

---

## 7. TaskWorker

```python
# magi/channels/workers/task.py

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from magi.channels.workers.base import ChannelWorker

if TYPE_CHECKING:
    from magi.new_bus import NewBus
    from magi.new_bus.guild.chatJob import ChatJob

logger = logging.getLogger("magi.channels.task.worker")


class TaskWorker(ChannelWorker):
    """Task 通道 Worker：定时轮询 → 触发 ChatJob。

    每 :attr:`poll_seconds` 检查一次 task_records Book，
    发现有到期的任务（cron 匹配当前分钟 / run_at 已过）→
    构建 context prompt → 追加 Session → 发布 ChatJob。

    职责边界：
    - 只负责"何时触发"和"发什么 prompt"。不等待 Agent 回复。
    - 不处理 delivery — 任务触发的 Agent 回复走 delivery_job_board，
      由对应 channel 的 worker 投递。
    - 不替代 APScheduler 的所有功能（如 misfire 策略），
      简单 cron 匹配 + run_at 判断即可满足需求。

    替代当前：channels/tasks/scheduler.py + channels/tasks/runner.py。
    """

    channel_name = "task"

    def __init__(
        self,
        bus: NewBus,
        *,
        poll_seconds: float = 15.0,  # 任务调度不需要高精度，15s 足够
    ) -> None:
        super().__init__(bus, poll_seconds=poll_seconds)
        # 记录每个 task 上次触发的时间，避免 15s 内重复触发
        self._last_fire: dict[str, datetime] = {}
        # 启动时 rehydrate
        self._rehydrated = False

    async def _run(self) -> None:
        """轮询 task_records Book，检查是否有到期任务。"""
        # 启动时恢复已启用的任务
        self._rehydrate()
        self._rehydrated = True

        while not self._stopping:
            try:
                tasks = self._list_enabled_tasks()
                now = datetime.now(timezone.utc)
                for task in tasks:
                    if self._should_fire(task, now):
                        await self._fire_task(task, now)
            except Exception:
                logger.exception("TaskWorker: iteration failed")
            await asyncio.sleep(self.poll_seconds)

    def _list_enabled_tasks(self) -> list:
        """从 task_records Book 读取所有启用的任务。"""
        # 具体 API 取决于 new_bus 中 task Book 的接口
        return self.bus.task_records_book.list_enabled()

    def _should_fire(self, task, now: datetime) -> bool:
        """判断任务是否应该在 now 时刻触发。

        支持两种触发方式：
        1. cron: ``task.cron`` 非空 → 检查当前分钟是否匹配
        2. run_at: ``task.run_at`` 非空 → 检查是否已过且未触发过
        """
        task_id = task.id

        # run_at 模式（一次性）
        if task.run_at:
            run_at = self._parse_datetime(task.run_at)
            if run_at and run_at <= now:
                last = self._last_fire.get(task_id)
                if last is None or last < run_at:
                    return True
            return False

        # cron 模式
        if task.cron:
            import croniter
            cron = croniter.croniter(task.cron, now)
            prev_fire = cron.get_prev(datetime)
            last = self._last_fire.get(task_id)
            if last is None or (prev_fire and prev_fire > last):
                return True
            return False

        return False

    async def _fire_task(self, task, now: datetime) -> None:
        """触发一个任务：构建 prompt → 发布 ChatJob。"""
        task_id = task.id

        # 构建上下文 prompt（对齐当前 runner.py 的 contextual_prompt）
        schedule_desc = (
            task.cron if task.cron
            else f"once at {task.run_at}" if task.run_at
            else "ad-hoc"
        )
        contextual_prompt = (
            f"[task context]\n"
            f"You are EXECUTING a scheduled task that just fired.\n"
            f"name: {task.name}\n"
            f"schedule: {schedule_desc}\n"
            f"channel: {task.target_channel}\n\n"
            f"[task prompt]\n{task.prompt}"
        )

        # 追加到 Session（如果 session 存在）
        if task.session_id:
            self.bus.messages_book.append(
                uid=task.uid,
                session_id=task.session_id,
                role="user",
                text=contextual_prompt,
            )

        # 发布 ChatJob
        try:
            await self.bus.agent_job_board.publish(ChatJob(
                external_id=f"task:{task_id}:{now.timestamp()}",
                source_type="task",
                text=contextual_prompt,
                channel="task",
                session_id=task.session_id,
                uid=task.uid,
                caller_role=task.caller_role,
                metadata={"task_id": task_id, "fired_at": now.isoformat()},
            ))
        except Exception:
            logger.exception("TaskWorker: failed to publish ChatJob for task %s", task_id)
            return

        self._last_fire[task_id] = now
        logger.info("TaskWorker: fired task %s at %s", task_id, now.isoformat())

    def _rehydrate(self) -> None:
        """启动时恢复已启用任务的 last_fire 状态。"""
        tasks = self._list_enabled_tasks()
        logger.info("TaskWorker: rehydrated %d enabled task(s)", len(tasks))

    @staticmethod
    def _parse_datetime(s: str) -> datetime | None:
        try:
            return datetime.fromisoformat(s)
        except (ValueError, TypeError):
            return None
```

---

## 8. WebUIWorker

```python
# magi/channels/workers/webui.py

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from magi.channels.workers.base import ChannelWorker

if TYPE_CHECKING:
    from magi.new_bus import NewBus
    from magi.new_bus.guild.deliveryJob import DeliveryJob, DeliveryResult

logger = logging.getLogger("magi.channels.webui.worker")


class WebUIWorker(ChannelWorker):
    """WebUI 通道 Worker：纯出站，认领 webui 的 DeliveryJob 写入 Session。

    职责（替代 DeliveryWorker 的 ``claim.channel=="webui"`` 分支）：
    1. 从 delivery_job_board 认领 channel=="webui" 的 Job
    2. 将消息内容追加到对应 Session（通过 messages_book）
    3. submit_result 标记完成

    注意：WebUI 的入站由 FastAPI HTTP 路由处理，不是本 worker 的职责。
    """

    channel_name = "webui"

    async def _run(self) -> None:
        while not self._stopping:
            try:
                job = await asyncio.to_thread(
                    self.bus.delivery_job_board.claim, channel="webui"
                )
            except Exception:
                logger.exception("WebUIWorker: delivery claim failed")
                await asyncio.sleep(self.poll_seconds)
                continue

            if job is None:
                await asyncio.sleep(self.poll_seconds)
                continue

            try:
                await self._deliver_webui(job)
            except Exception:
                logger.exception("WebUIWorker: delivery %s failed", job.job_id)
                self.bus.delivery_job_board.submit_result(
                    key=job.job_id,
                    result=DeliveryResult(job_id=job.job_id, success=False, error="append failed"),
                )
            else:
                self.bus.delivery_job_board.submit_result(
                    key=job.job_id,
                    result=DeliveryResult(job_id=job.job_id, success=True),
                )

    async def _deliver_webui(self, job: DeliveryJob) -> None:
        """将 delivery 内容追加到 Session 消息。

        对齐当前 DeliveryWorker 中 channel=="webui" 的处理逻辑：
        - 从 job.payload 提取 session_id, uid, text
        - 调用 messages_book.append 写入
        """
        session_id = str(job.payload.get("session_id") or "")
        uid = job.payload.get("uid")
        text = str(job.payload.get("text") or "")

        if not session_id or uid is None:
            raise ValueError("webui delivery missing session_id or uid")

        self.bus.messages_book.append(
            uid=int(uid),
            session_id=session_id,
            role="assistant",
            text=text,
        )
        logger.debug(
            "WebUIWorker: appended message to session %s (uid=%s)",
            session_id, uid,
        )
```

---

## 9. A2AWorker

```python
# magi/channels/workers/a2a.py

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from magi.channels.workers.base import ChannelWorker

if TYPE_CHECKING:
    from magi.new_bus import NewBus
    from magi.new_bus.guild.deliveryJob import DeliveryJob, DeliveryResult

logger = logging.getLogger("magi.channels.a2a.worker")


class A2AWorker(ChannelWorker):
    """A2A 通道 Worker：出站投递到对等 MAGI。

    职责（替代 DeliveryWorker 的 ``claim.channel=="a2a"`` 分支）：
    从 delivery_job_board 认领 channel=="a2a" 的 Job →
    HTTP POST 到目标 MAGI → submit_result。

    注意：A2A 入站由 FastAPI ``POST /a2a/inbox`` 路由处理，
    不在本 worker 范围内。
    """

    channel_name = "a2a"

    async def _run(self) -> None:
        while not self._stopping:
            try:
                job = await asyncio.to_thread(
                    self.bus.delivery_job_board.claim, channel="a2a"
                )
            except Exception:
                logger.exception("A2AWorker: delivery claim failed")
                await asyncio.sleep(self.poll_seconds)
                continue

            if job is None:
                await asyncio.sleep(self.poll_seconds)
                continue

            try:
                await self._deliver_a2a(job)
            except Exception:
                logger.exception("A2AWorker: delivery %s failed", job.job_id)
                self.bus.delivery_job_board.submit_result(
                    key=job.job_id,
                    result=DeliveryResult(job_id=job.job_id, success=False, error="send failed"),
                )
            else:
                self.bus.delivery_job_board.submit_result(
                    key=job.job_id,
                    result=DeliveryResult(job_id=job.job_id, success=True),
                )

    async def _deliver_a2a(self, job: DeliveryJob) -> None:
        """HTTP POST 到目标 MAGI。

        复用当前 channels/a2a/transport.py:send_a2a_delivery 的逻辑。
        """
        from magi.channels.a2a.transport import send_a2a_delivery

        await send_a2a_delivery(
            int(job.destination),
            job.job_id,
            job.payload,
        )
```

---

## 10. DeliveryWorker 废弃计划

### 10.1 阶段

| 阶段 | 内容 | 标志 |
|------|------|------|
| **Phase 1** | 新增四个 Channel Worker，与 DeliveryWorker 并行运行 | 新旧 delivery 处理重叠，需幂等 |
| **Phase 2** | Channel Worker 成熟后，DeliveryWorker 停止认领，仅保留代码 | 不再消费 delivery_job_board |
| **Phase 3** | 删除 `channels/delivery.py` 和旧 `DeliveryWorker` 类 | 所有 delivery 由 Channel Worker 处理 |

### 10.2 Phase 1 重叠期的幂等处理

由于 Phase 1 中 Channel Worker 和 DeliveryWorker 都消费 `delivery_job_board`，需要确保一条 deliveryJob 只被一个 worker 认领。这由 job board 的 `claim()` 内置的租约机制保证——同一条 job 被 claim 后状态变为 `claimed`，其他 worker 无法再次认领。无需额外的幂等逻辑。

### 10.3 过渡期配置

可通过 `NewBus` 的 `settings_book` 配置项控制：

```
"channels.delivery.mode": "channel_workers" | "legacy" | "both"
```

- `"channel_workers"`: 仅 Channel Worker 处理（Phase 2 起）
- `"legacy"`: 仅 DeliveryWorker 处理（回退用）
- `"both"`: 两者都跑（Phase 1，默认）

---

## 11. 启动与组合根

### 11.1 模块级单例

每个 Channel Worker 遵循现有模块级单例模式：

```python
# magi/channels/workers/__init__.py

_telegram: TelegramWorker | None = None
_task: TaskWorker | None = None
_webui: WebUIWorker | None = None
_a2a: A2AWorker | None = None


async def start_channel_workers(bus: NewBus, *, enabled: set[str]) -> dict[str, ChannelWorker]:
    """启动已启用的 Channel Worker。

    返回 {channel_name: worker} 映射，供调用方管理生命周期。
    """
    workers: dict[str, ChannelWorker] = {}

    if "telegram" in enabled:
        workers["tg"] = await start_telegram_worker(bus)

    if "scheduled" in enabled:
        workers["task"] = await start_task_worker(bus)

    if "webui" in enabled:
        workers["webui"] = await start_webui_worker(bus)

    if "a2a" in enabled:
        workers["a2a"] = await start_a2a_worker(bus)

    return workers


async def stop_channel_workers(workers: dict[str, ChannelWorker]) -> None:
    """逆序停止所有 Channel Worker。"""
    for name, worker in reversed(list(workers.items())):
        try:
            await worker.stop()
        except Exception:
            logger.exception("channel worker %s stop failed", name)


async def start_telegram_worker(bus: NewBus) -> TelegramWorker:
    global _telegram
    if _telegram is None:
        _telegram = TelegramWorker(bus)
        await _telegram.start()
    return _telegram


async def start_task_worker(bus: NewBus) -> TaskWorker:
    global _task
    if _task is None:
        _task = TaskWorker(bus)
        await _task.start()
    return _task


async def start_webui_worker(bus: NewBus) -> WebUIWorker:
    global _webui
    if _webui is None:
        _webui = WebUIWorker(bus)
        await _webui.start()
    return _webui


async def start_a2a_worker(bus: NewBus) -> A2AWorker:
    global _a2a
    if _a2a is None:
        _a2a = A2AWorker(bus)
        await _a2a.start()
    return _a2a
```

### 11.2 集成到 `_runtime_lifespan`

```python
# magi/startup/runtime.py — 修改

async def _runtime_lifespan(workers, channels, new_bus, magi_id):
    # 现有 worker 不变
    await start_provider_worker(bus=new_bus)
    await start_tool_worker(bus=new_bus)
    await start_mcp_worker(bus=new_bus)
    await start_agent_worker(bus=new_bus)
    await start_proactive_worker(bus=new_bus, magi_id=magi_id)

    # 新：Channel Workers（替代 Telegram daemon thread + Task scheduler）
    from magi.channels.workers import start_channel_workers, stop_channel_workers
    channel_workers = await start_channel_workers(new_bus, enabled=set(channels))

    # 过渡期：DeliveryWorker 仍然启动
    await start_delivery_worker()

    try:
        yield
    finally:
        # 逆序停止
        await stop_delivery_worker()
        await stop_channel_workers(channel_workers)
        await stop_proactive_worker()
        await stop_agent_worker()
        await stop_mcp_worker()
        await stop_tool_worker()
        await stop_provider_worker()
```

---

## 12. 数据流总览

### 12.1 完整请求生命周期（以 Telegram 为例）

```
  TG 用户发消息
       │
       ▼
  TelegramWorker._on_tg_message
       │
       ├─ self.bus.contacts_book.find(telegram_id=...)    # 解析联系人
       ├─ self.bus.sessions_book.create(...)               # 创建/复用 Session
       ├─ self.bus.messages_book.append(...)               # 追加用户消息
       │
       └─ self.bus.agent_job_board.publish(ChatJob(       # 发布 ChatJob
              channel="tg",
              text="用户说...",
              session_id="...",
              uid=42,
          ))
              │
              ▼
       AgentWorker._run()
              │
              ├─ claim ChatJob
              ├─ 读取 session 历史 (self.bus.messages_book.get_messages)
              ├─ 调用 LLM (self.bus.llm_job_board.publish)
              ├─ 处理工具调用 (self.bus.tool_job_board.publish)
              │
              └─ self.bus.delivery_job_board.publish(     # 发布 DeliveryJob
                     DeliveryJob(
                         channel="tg",
                         destination="123456",            # TG chat_id
                         payload={"text": "回复内容..."},
                     ))
                         │
                         ▼
              TelegramWorker._run_outbound()
                         │
                         ├─ claim DeliveryJob(channel="tg")
                         ├─ HTTP POST sendMessage → TG API
                         └─ submit_result(success=True)
                              │
                              ▼
                        TG 用户看到回复
```

### 12.2 数据流简化图

```
External Input                    MAGI Internal                    External Output
═══════════════                   ═════════════                    ════════════════

TG User ──▶ TelegramWorker ──▶ ChatJob ──▶ AgentWorker
                                              │
Task Cron ──▶ TaskWorker ──▶ ChatJob ──▶      │
                                              │
A2A HTTP  ──▶ FastAPI Route ──▶ ChatJob ──▶   │
                                              │
WebUI HTTP──▶ FastAPI Route ──▶ ChatJob ──▶   │
                                              ▼
                                     delivery_job_board
                                              │
                         ┌────────────────────┼────────────────────┐
                         ▼                    ▼                    ▼
                  TelegramWorker        WebUIWorker          A2AWorker
                         │                    │                    │
                         ▼                    ▼                    ▼
                     TG API             Session 追加         Peer MAGI
```

---

## 13. 迁移路径

### 13.1 迁移顺序

1. **先建基类 `ChannelWorker`**——`channels/workers/base.py`
2. **再建 WebUIWorker**——最简单，纯出站，风险最低，可最先验证模式
3. **再建 TaskWorker**——中复杂度，替代 APScheduler，需验证 cron/run_at 逻辑
4. **再建 TelegramWorker**——高复杂度，需要移植 TG 长轮询逻辑到 worker event loop
5. **最后建 A2AWorker**——当前已禁用，不影响线上
6. **逐步废弃 DeliveryWorker**

### 13.2 回退策略

- 每个 Channel Worker 启动前检查 `settings_book` 的 feature flag
- 如果新 worker 出现问题，关闭对应 flag，回退到旧 `DeliveryWorker`
- 旧代码（`bot.py` 守护线程、`scheduler.py` APScheduler）在 Phase 2 前**不删除**，仅标记 `deprecated`

### 13.3 测试矩阵

| Worker | 测试重点 |
|--------|---------|
| `ChannelWorker` (base) | start/stop 幂等性，取消语义 |
| `TelegramWorker` | TG 消息解析，联系人路由，Session 复用，delivery 发货 |
| `TaskWorker` | cron 匹配精度，run_at 一次性触发，重启动后 rehydrate，15s 去重 |
| `WebUIWorker` | session_id/uid 校验，消息追加，delivery 失败重试 |
| `A2AWorker` | HMAC 签名，HTTP 超时，目标不可达 |

---

## 14. 风险与注意事项

### 14.1 Telegram 的 event loop 问题

当前 Telegram bot 跑在守护线程的独立 event loop 中（`asyncio.run(_run_forever())`）。迁移到 `TelegramWorker` 后，bot 将在主 event loop 中运行，与 FastAPI 共享同一个 loop。**需要验证** `Application.run_polling` 在主 event loop 中是否正常工作（ptyhon-telegram-bot 的 `Application.start()` + `updater.start_polling()` 已支持共享 event loop）。

### 14.2 chatJob 与 deliveryJob 的形态

当前使用旧总线的 `AgentMessage` 入队。`ChatJob` 和 `DeliveryJob` 的字段形态需与 `new_bus` 的 guild 定义对齐。如果 guild 尚未定义这些类型，需先创建。

### 14.3 过渡期双重 Delivery

Phase 1 期间，Channel Worker 和旧 `DeliveryWorker` 同时消费 `delivery_job_board`。Job Board 的 claim 租约机制保证同一条 job 不会被两个 worker 同时认领，但需要注意 **poll_seconds 差异**——Channel Worker 如果 poll 更快，会抢在 DeliveryWorker 之前认领，可能导致行为变化（但逻辑是等价的）。

### 14.4 Task 的 APScheduler 替换

`TaskWorker` 用简单轮询替代 APScheduler，功能上有差距：
- **无 misfire 策略。** APScheduler 的 `misfire_grace_time`（300s 内补触发）在新方案中需自行实现。
- **无 coalesce 保护。** 多个 cron 触发重叠时 APScheduler 会合并，`TaskWorker` 靠 `_last_fire` 去重但粒度是 poll_seconds，仍有极小窗口重复触发。
- **权衡：** 对于"检查股票"这类任务，15s 精度足够；对于"秒级精度"的定时任务，APScheduler 仍然不可替代。可在 `TaskWorker` 中保留可选依赖。

### 14.5 联系人/会话/消息的数据来源

当前 Telegram 和 Task 的入站代码大量依赖 `magi.bus.Bus` 的 `.contacts`、`.session`、`.tasks` 等服务。迁移到 `new_bus` 后需改用对应的 Books（`contacts_book`、`sessions_book`、`messages_book`、`task_records_book`）。需确认这些 Book 的接口是否已就绪。

---

## 附录：文件结构

迁移后的文件布局：

```
magi/channels/
├── __init__.py
├── base.py                   # 旧 Channel Protocol（保留）
├── dispatcher.py             # ChannelAdapter 注册表（保留，逐步迁移到 new_bus）
├── delivery.py               # 旧 DeliveryWorker → Phase 3 删除
│
├── workers/                  # 新：Channel Worker 包
│   ├── __init__.py           # start_channel_workers / stop / 单例
│   ├── base.py               # ChannelWorker 基类
│   ├── telegram.py           # TelegramWorker
│   ├── task.py               # TaskWorker
│   ├── webui.py              # WebUIWorker
│   └── a2a.py                # A2AWorker
│
├── api/                      # FastAPI 路由（不变）
├── tasks/
│   ├── __init__.py
│   ├── channel.py            # TaskChannel（保留）
│   ├── runner.py             # execute_task → 逐步废弃
│   └── scheduler.py          # TaskScheduler → 逐步废弃
├── telegram/
│   ├── __init__.py           # 注册 TelegramAdapter（保留）
│   ├── adapter.py            # ChannelAdapter（保留）
│   ├── bot.py                # 旧 TG 监听器 → Phase 2 废弃
│   └── config.py             # 已读回执配置（保留）
└── a2a/
    ├── __init__.py
    ├── adapter.py
    ├── protocol.py           # HMAC 签名
    ├── router.py             # FastAPI /a2a/inbox 路由（保留）
    └── transport.py          # send_a2a_delivery（保留，由 A2AWorker 调用）
```

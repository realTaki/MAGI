"""ProactiveWorker — 最后一个拉起的 Worker，处理系统级主动策略。

启动流程
========

1. ``_bootstrap()`` — 判断本 MAGI 是否是所处 MAGIS 的 Adam；
   若是，对已有 admin 幂等插入 credentials nudge。
2. ``_run()`` — 主循环，claim SeedPresetTasksJob 并执行播种。

Credentials nudge 的策略逻辑（spec 选择 + 幂等检查 + INSERT）
直接内嵌在 :meth:`ProactiveWorker._bootstrap` 中。Worker 是
proactive 模块唯一保留的策略权威 ——
``magi.proactive.credentials_nudge`` /
``magi.proactive.contracts`` 已删除，spec 常量
:data:`CREDENTIALS_NUDGE` 与 :class:`CredentialsNudgeSpec`
内聚在本文件顶部，由 Worker bootstrap 直接消费。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from magi.new_bus.library.local.actionItemBook import SOURCE_PROACTIVE

if TYPE_CHECKING:
    from magi.new_bus import NewBus
    from magi.new_bus.guild.seedPresetTasksJob import (
        SeedPresetTasksJob,
    )

logger = logging.getLogger("magi.proactive.worker")


# -- Credentials nudge spec ------------------------------------------------
#
# Owned by the Worker — the legacy ``magi.proactive.credentials_nudge``
# module has been deleted; the policy decision (which spec to write,
# which provenance tag, what title makes the idempotency key stable)
# is the Worker's responsibility now.

@dataclass(frozen=True, slots=True)
class CredentialsNudgeSpec:
    """Static content for the credentials nudge.

    Frozen so the wizard, the dashboard renderer, and
    tests can introspect the spec without surprise
    mutations.  The stable ``title`` field is the
    idempotency key — the Worker's bootstrap hook uses it
    to skip already-open / already-completed rows.
    """

    title: str
    description: str
    target_url: str


# The one and only nudge. Stable ``title`` so the
# idempotency check (and any future partial unique
# index) match by exact string — callers and tests
# shouldn't need to know the rest of the content.
CREDENTIALS_NUDGE = CredentialsNudgeSpec(
    title="设置你的 LLM provider 和 API key",
    description=(
        "切到「Contacts」,找到自己的档案,"
        "把 Provider 和 API Key 填上。"
    ),
    target_url="/dashboard?tab=organization",
)


def ensure_for_admin(
    *,
    book: object,  # ActionItemBook (lazy to avoid import cycle)
    admin_id: int,
) -> bool:
    """Idempotently insert the credentials nudge for one admin.

    Returns ``True`` if a new row was created, ``False`` if
    an open nudge already exists for ``admin_id``.

    Used by both :meth:`ProactiveWorker._bootstrap` and the
    onboarding API (synchronous path, to be replaced by Job
    publish in a future pass).
    """
    spec = CREDENTIALS_NUDGE
    existing = [
        row
        for row in book.list_actions(
            owner_uid=admin_id,
            include_completed=False,
            source=SOURCE_PROACTIVE,
        )
        if row.title == spec.title
    ]
    if existing:
        logger.debug(
            "credentials_nudge: open nudge already exists for admin=%s; skipping",
            admin_id,
        )
        return False
    # 额外检查：是否已完成
    completed = [
        row
        for row in book.list_actions(
            owner_uid=admin_id,
            include_completed=True,
            source=SOURCE_PROACTIVE,
        )
        if row.title == spec.title and row.completed_at is not None
    ]
    if completed:
        return False
    book.add(
        uid=admin_id,
        title=spec.title,
        description=spec.description,
        target_url=spec.target_url,
        source=SOURCE_PROACTIVE,
    )
    logger.info(
        "credentials_nudge: inserted for admin=%s (title=%r)",
        admin_id, spec.title,
    )
    return True


class ProactiveWorker:
    """系统级主动策略的消费者。

    Receives a fully-wired :class:`NewBus` and the current
    ``magi_id`` via constructor injection.
    """

    def __init__(
        self,
        bus: "NewBus",
        *,
        magi_id: int | None = None,
        poll_seconds: float = 0.25,
    ) -> None:
        self.bus = bus
        self._magi_id = magi_id
        self.poll_seconds = poll_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    async def start(self) -> None:
        if self._task is not None:
            return

        # 1. Bootstrap: Adam 检查 + admin credentials nudge
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

    # ------------------------------------------------------------------
    # main loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        while not self._stopping:
            try:
                job = await asyncio.to_thread(
                    self.bus.seed_preset_tasks_job_board.claim,
                )
            except Exception:
                logger.exception("proactive worker: claim failed")
                await asyncio.sleep(self.poll_seconds)
                continue
            if job is None:
                await asyncio.sleep(self.poll_seconds)
                continue

            await self._handle_seed_job(job)

    # ------------------------------------------------------------------
    # bootstrap
    # ------------------------------------------------------------------

    async def _bootstrap(self) -> None:
        """启动时：如果本 MAGI 是 Adam，对已有 admin 幂等插入 credentials nudge。"""
        magis_id = self._resolve_magis_id()
        if magis_id is None:
            return  # 没有 MAGIS DB，跳过

        if not self._is_adam(magis_id):
            logger.info("proactive worker: not Adam, skipping bootstrap")
            return

        magis_admins_book = getattr(self.bus, "magis_admins_book", None)
        if magis_admins_book is None:
            return
        admin_rows = magis_admins_book.list_for_magis(magis_id=magis_id)
        if not admin_rows:
            logger.info(
                "proactive worker: no admins for magis_id=%d, skipping", magis_id
            )
            return

        for entry in admin_rows:
            uid = entry.uid  # contacts.id
            inserted = ensure_for_admin(
                book=self.bus.action_items_book,
                admin_id=uid,
            )
            if inserted:
                logger.info(
                    "proactive worker: bootstrap nudge inserted for admin uid=%d",
                    uid,
                )

    # ------------------------------------------------------------------
    # MAGIS identity
    # ------------------------------------------------------------------

    def _resolve_magis_id(self) -> int | None:
        """解析本 MAGI 所属的 MAGIS id。"""
        magi_id = self._magi_id
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
        magi_id = self._magi_id
        if magi_id is None:
            return False
        magis_book = getattr(self.bus, "magis_book", None)
        if magis_book is None:
            return False
        magis_node = magis_book.get(magis_id=magis_id)
        if magis_node is None:
            return False
        return magis_node.adam_id == magi_id

    # ------------------------------------------------------------------
    # seed job handling
    # ------------------------------------------------------------------

    async def _handle_seed_job(self, job: "SeedPresetTasksJob") -> None:
        """处理 SeedPresetTasksJob：执行预设任务播种。

        当前桥接旧 bus 的 TaskService.seed_presets_for_contact()。
        待 TaskPreset schema 迁移到 new_bus 后，改为直接操作
        tasks_book 和纯策略函数 plan_presets_for_contact()。
        """
        try:
            # Bridge: delegate to old bus TaskService.
            # TODO(proactive-refactor): migrate to new_bus TaskBook
            # once TaskPreset schema is unified in the new bus ORM.
            from magi.bus import get_bus

            old_bus = get_bus()
            inserted = old_bus.task.seed_presets_for_contact(job.contact_id)

            from magi.new_bus.guild.seedPresetTasksJob import SeedPresetTasksResult

            result = SeedPresetTasksResult(
                job_id=job.job_id,
                success=True,
                inserted=inserted,
                skipped=0,
            )
            self.bus.seed_preset_tasks_job_board.submit_result(
                key=job.job_id, result=result
            )

        except Exception as exc:
            logger.exception(
                "proactive worker: seed job %s failed", job.job_id
            )
            self._submit_seed_failure(job, str(exc))

    def _submit_seed_failure(self, job: "SeedPresetTasksJob", error: str) -> None:
        from magi.new_bus.guild.seedPresetTasksJob import SeedPresetTasksResult

        try:
            result = SeedPresetTasksResult(
                job_id=job.job_id,
                success=False,
                error=error[:8000],
            )
            self.bus.seed_preset_tasks_job_board.submit_result(
                key=job.job_id, result=result
            )
        except Exception:
            logger.exception(
                "proactive worker: failed to submit seed failure for %s",
                job.job_id,
            )


# -- module-level singleton ------------------------------------------------


_worker: ProactiveWorker | None = None


async def start_proactive_worker(
    bus: "NewBus",
    *,
    magi_id: int | None = None,
) -> ProactiveWorker:
    """Start the process-local proactive worker.

    ``magi_id`` is the per-MAGI identity; ``None`` skips
    Adam-dependent bootstrap checks.
    """
    global _worker
    if _worker is None:
        _worker = ProactiveWorker(bus=bus, magi_id=magi_id)
        await _worker.start()
    return _worker


async def stop_proactive_worker() -> None:
    global _worker
    if _worker is not None:
        await _worker.stop()
        _worker = None


__all__ = [
    "CredentialsNudgeSpec",
    "CREDENTIALS_NUDGE",
    "ensure_for_admin",
    "ProactiveWorker",
    "start_proactive_worker",
    "stop_proactive_worker",
]

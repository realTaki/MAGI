"""BUS-owned durable task storage and scheduling validation."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from magi.bus.contracts.task import TaskExecution, TaskPresetView, TaskScheduleView


def _schedule_view(row) -> TaskScheduleView:
    return TaskScheduleView(
        id=str(row.id), enabled=bool(row.enabled), cron=str(row.cron), run_at=row.run_at,
    )


def _preset_view(row) -> TaskPresetView:
    return TaskPresetView(
        id=str(row.id), key=str(row.key), name=str(row.name), description=str(row.description),
        prompt=str(row.prompt), frequency=str(row.frequency), hour=int(row.hour), minute=int(row.minute),
        day_of_week=row.day_of_week, day_of_month=row.day_of_month, run_at=row.run_at,
        target_channel=str(row.target_channel), enabled=bool(row.enabled),
        created_at=str(row.created_at), updated_at=str(row.updated_at),
    )


class TaskService:
    """Task persistence façade; channel schedulers observe committed rows."""

    def __init__(self, state_dir: str) -> None:
        self._state_dir = state_dir

    # -- read -----------------------------------------------------------

    def get_schedule(self, task_id: str) -> TaskScheduleView | None:
        from magi.db import open_session
        from magi.bus.models.local.task import Task
        with open_session(self._state_dir) as session:
            row = session.get(Task, str(task_id))
            return _schedule_view(row) if row is not None else None

    def list_enabled_schedules(self) -> list[TaskScheduleView]:
        from magi.db import open_session
        from magi.bus.models.local.task import Task
        from sqlalchemy import select
        with open_session(self._state_dir) as session:
            rows = session.scalars(
                select(Task).where(Task.enabled.is_(True))
            ).all()
            return [_schedule_view(row) for row in rows]

    # -- preset templates ----------------------------------------------

    def list_presets(self) -> list[TaskPresetView]:
        from sqlalchemy import select
        from magi.bus.models.local.task_preset import TaskPreset
        from magi.db import open_session

        with open_session(self._state_dir) as session:
            return [_preset_view(row) for row in session.scalars(
                select(TaskPreset).order_by(TaskPreset.key)
            )]

    def get_preset(self, preset_id: str) -> TaskPresetView | None:
        from magi.bus.models.local.task_preset import TaskPreset
        from magi.db import open_session

        with open_session(self._state_dir) as session:
            row = session.get(TaskPreset, preset_id)
            return _preset_view(row) if row is not None else None

    def create_preset(self, **values) -> TaskPresetView | None:
        from sqlalchemy import select
        from magi.bus.models.local.task_preset import TaskPreset
        from magi.bus.contracts.session import new_session_id
        from magi.db import open_session

        with open_session(self._state_dir) as session:
            if session.scalar(select(TaskPreset.id).where(TaskPreset.key == values["key"])) is not None:
                return None
            now = datetime.utcnow().isoformat()
            row = TaskPreset(
                id=new_session_id(), key=values["key"], name=values["name"],
                description=values["description"], prompt=values["prompt"],
                frequency=values["frequency"], hour=values["hour"], minute=values["minute"],
                day_of_week=values.get("day_of_week"), day_of_month=values.get("day_of_month"),
                run_at=values.get("run_at"), target_channel=values["target_channel"],
                enabled=int(bool(values["enabled"])), created_at=now, updated_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _preset_view(row)

    def update_preset(self, preset_id: str, **changes) -> TaskPresetView | None:
        from magi.bus.models.local.task_preset import TaskPreset
        from magi.db import open_session

        with open_session(self._state_dir) as session:
            row = session.get(TaskPreset, preset_id)
            if row is None:
                return None
            for field, value in changes.items():
                if field == "enabled":
                    value = int(bool(value))
                setattr(row, field, value)
            row.updated_at = datetime.utcnow().isoformat()
            session.commit()
            session.refresh(row)
            return _preset_view(row)

    def delete_preset(self, preset_id: str) -> bool:
        from magi.bus.models.local.task_preset import TaskPreset
        from magi.db import open_session

        with open_session(self._state_dir) as session:
            row = session.get(TaskPreset, preset_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    # -- write ----------------------------------------------------------

    def upsert_by_name(
        self,
        *,
        name: str,
        prompt: str,
        cron: str,
        run_at: Optional[str],
        delivery_to: Optional[str],
        target_channel: str,
        uid: int,
        session_id: str,
        tz: str,
        enabled: int = 1,
        consecutive_failures: int = 0,
    ) -> tuple[str, bool]:
        """Upsert a task by name; returns ``(task_id, is_update)``.

        The full upsert-by-name flow that ``magi.tools.schedule_task``
        and the WebUI task API share, including the cron / run_at
        / delivery_to / session_id / tz fields.
        """
        from magi.db import open_session
        from magi.bus.models.local.task import Task
        from sqlalchemy import select
        new_id = _new_task_id()
        with open_session(self._state_dir) as session:
            existing = session.execute(
                select(Task).where(Task.name == name)
            ).scalar_one_or_none()
            if existing is not None:
                existing.prompt = prompt
                existing.cron = cron
                existing.run_at = run_at
                existing.delivery_to = delivery_to
                existing.target_channel = target_channel
                existing.enabled = enabled
                existing.consecutive_failures = consecutive_failures
                existing.uid = uid
                if existing.session_id is None:
                    existing.session_id = session_id
                session.commit()
                return existing.id, True
            row = Task(
                id=new_id,
                name=name,
                prompt=prompt,
                cron=cron,
                run_at=run_at,
                delivery_to=delivery_to,
                session_id=session_id,
                tz=tz,
                target_channel=target_channel,
                uid=uid,
                enabled=enabled,
                consecutive_failures=consecutive_failures,
                created_at=datetime.utcnow().isoformat(),
                updated_at=datetime.utcnow().isoformat(),
            )
            session.add(row)
            session.commit()
            return new_id, False

    def prepare_execution(
        self, *, task_id: str, run_id: str, started_at: str, manual: bool,
    ) -> TaskExecution | None:
        """Atomically create a run and return the task context for channel I/O.

        The returned record is a DTO snapshot.  The caller publishes it to the
        actor only after this transaction commits; it never receives ORM rows.
        """
        from magi.bus.models.local.action_item import ActionItem
        from magi.bus.models.local.contact import Contact
        from magi.bus.models.local.session import ChatSession
        from magi.bus.models.local.task import Task, TaskRun
        from magi.bus.contracts.session import new_session_id, utcnow_iso
        from magi.db import open_session

        with open_session(self._state_dir) as session:
            task = session.get(Task, task_id)
            if task is None:
                return None
            contact = session.get(Contact, task.uid)
            if contact is None:
                finished_at = datetime.utcnow().isoformat()
                session.add(TaskRun(
                    id=run_id, task_id=task_id, session_id=None,
                    trigger="manual" if manual else "cron", started_at=started_at,
                    finished_at=finished_at, status="failed", error="contact_missing",
                    latency_ms=_milliseconds(started_at, finished_at),
                ))
                session.add(ActionItem(
                    uid=task.uid, kind="task_disabled",
                    title=f"定时任务无法执行：{task.name}",
                    description="任务所属联系人不存在。",
                    target_url=f"/chat/scheduled-tasks?task={task.id}",
                    priority="high", source="system",
                ))
                session.commit()
                return None
            if task.session_id is None:
                task.session_id = new_session_id()
                session.add(ChatSession(
                    session_id=task.session_id, delivery_address="", uid=task.uid,
                    channel="task", title=f"[定时] {task.name}",
                    created_at=utcnow_iso(), updated_at=utcnow_iso(),
                ))
            run = session.get(TaskRun, run_id)
            if run is None:
                session.add(TaskRun(
                    id=run_id, task_id=task_id, session_id=task.session_id,
                    trigger="manual" if manual else "cron", started_at=started_at,
                    status="running",
                ))
            session.commit()
            return TaskExecution(
                task_id=str(task.id), run_id=run_id, session_id=str(task.session_id),
                uid=int(task.uid), caller_role=contact.role, task_name=str(task.name),
                prompt=str(task.prompt), cron=str(task.cron), run_at=task.run_at,
                tz=str(task.tz), target_channel=str(task.target_channel),
                delivery_to=task.delivery_to,
            )

    def set_session_delivery_address(self, *, session_id: str, delivery_address: str) -> None:
        """Persist a channel-resolved address after its external lookup."""
        if not delivery_address:
            return
        from magi.bus.models.local.session import ChatSession
        from magi.db import open_session

        with open_session(self._state_dir) as session:
            row = session.get(ChatSession, session_id)
            if row is not None and not row.delivery_address:
                row.delivery_address = delivery_address
                session.commit()

    def mark_execution_failure(
        self, *, task_id: str, run_id: str, started_at: str, error: str,
    ) -> None:
        """Durably record an execution failure and one-way auto-disable edge."""
        from magi.bus.models.local.action_item import ActionItem
        from magi.bus.models.local.task import Task, TaskRun
        from magi.db import open_session
        from magi.db.settings import state_get

        finished_at = datetime.utcnow().isoformat()
        with open_session(self._state_dir) as session:
            run = session.get(TaskRun, run_id)
            task = session.get(Task, task_id)
            if run is not None:
                run.status = "failed"
                run.finished_at = finished_at
                run.latency_ms = _milliseconds(started_at, finished_at)
                run.error = error[:500]
            if task is not None:
                task.consecutive_failures = (task.consecutive_failures or 0) + 1
                task.last_status = "failed"
                task.last_run_at = finished_at
                task.last_error = error[:500]
                raw_threshold = state_get(self._state_dir, "task.failure_threshold")
                try:
                    threshold = max(1, int(raw_threshold or "5"))
                except ValueError:
                    threshold = 5
                if task.enabled and task.consecutive_failures >= threshold:
                    task.enabled = 0
                    session.add(ActionItem(
                        uid=task.uid, kind="task_disabled",
                        title=f"定时任务已自动停用：{task.name}",
                        description=(f"连续失败 {task.consecutive_failures} 次（阈值 {threshold}）。"
                                     f"最后一次错误：{error[:200]}"),
                        target_url=f"/chat/scheduled-tasks?task={task.id}",
                        priority="high", source="system",
                    ))
            session.commit()

    # -- cron / schedule validation ------------------------------------
    @staticmethod
    def preset_to_cron(*args, **kwargs):
        from magi.bus.task_schedule import preset_to_cron
        return preset_to_cron(*args, **kwargs)

    @staticmethod
    def validate_run_at(*args, **kwargs):
        from magi.bus.task_schedule import validate_run_at
        return validate_run_at(*args, **kwargs)

    @staticmethod
    def validate_run_at_future(*args, **kwargs):
        from magi.bus.task_schedule import validate_run_at_future
        return validate_run_at_future(*args, **kwargs)


def _new_task_id() -> str:
    import uuid
    return f"task_{uuid.uuid4().hex}"


def _milliseconds(started_at: str, finished_at: str) -> int:
    try:
        return max(0, int((datetime.fromisoformat(finished_at) - datetime.fromisoformat(started_at)).total_seconds() * 1000))
    except ValueError:
        return 0

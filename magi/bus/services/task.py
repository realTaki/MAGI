"""BUS-owned durable task storage and scheduling validation."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from magi.bus.contracts.task import TaskScheduleView


def _schedule_view(row) -> TaskScheduleView:
    return TaskScheduleView(
        id=str(row.id), enabled=bool(row.enabled), cron=str(row.cron), run_at=row.run_at,
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

"""Bus service: task (CRUD on Task / TaskRun + live scheduler registration)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from magi.bus.contracts.session import Session, SessionMessage


class TaskService:
    """Task entity façade; live scheduler is in :mod:`magi.channels.tasks.scheduler`."""

    def __init__(self, state_dir: str) -> None:
        self._state_dir = state_dir

    # -- read -----------------------------------------------------------

    def get(self, task_id: str):
        from magi.db import open_session
        from magi.channels.tasks.models import Task
        with open_session(self._state_dir) as session:
            return session.get(Task, str(task_id))

    def list_enabled(self):
        from magi.db import open_session
        from magi.channels.tasks.models import Task
        from sqlalchemy import select
        with open_session(self._state_dir) as session:
            return list(session.scalars(
                select(Task).where(Task.enabled.is_(True))
            ).all())

    def get_by_name(self, name: str):
        from magi.db import open_session
        from magi.channels.tasks.models import Task
        from sqlalchemy import select
        with open_session(self._state_dir) as session:
            return session.execute(
                select(Task).where(Task.name == name)
            ).scalar_one_or_none()

    # -- write ----------------------------------------------------------

    def upsert(self, task) -> None:
        """Persist a Task row; the live scheduler is the caller's job to register."""
        from magi.db import open_session
        from magi.channels.tasks.models import Task
        with open_session(self._state_dir) as session:
            existing = session.get(Task, str(task.id))
            if existing is None:
                session.add(task)
            else:
                # Copy fields from incoming onto existing
                for col in task.__table__.columns:
                    setattr(existing, col.name, getattr(task, col.name))
            session.commit()

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
        from magi.channels.tasks.models import Task
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

    # -- live registration ---------------------------------------------

    def live_register(self, task_id: str) -> None:
        from magi.channels.tasks.scheduler import get_scheduler
        get_scheduler().reschedule(task_id)

    def live_register_from_db(self, task_id: str) -> bool:
        """Re-read the task from DB and register it with the live scheduler.

        Returns ``True`` if registration was attempted, ``False`` if
        the scheduler is not running.
        """
        from magi.channels.tasks.scheduler import get_scheduler
        try:
            scheduler = get_scheduler()
        except RuntimeError:
            return False
        task = self.get(task_id)
        if task is not None:
            scheduler.register(task)
        return True

    # -- cron / schedule validation ------------------------------------

    # These wrap the pure ``magi.channels.tasks.cron_utils`` helpers so
    # the tools / agent layer can validate a cron expression without
    # importing from ``magi.channels`` directly.
    @staticmethod
    def preset_to_cron(*args, **kwargs):
        from magi.channels.tasks.cron_utils import preset_to_cron
        return preset_to_cron(*args, **kwargs)

    @staticmethod
    def validate_run_at(*args, **kwargs):
        from magi.channels.tasks.cron_utils import validate_run_at
        return validate_run_at(*args, **kwargs)

    @staticmethod
    def validate_run_at_future(*args, **kwargs):
        from magi.channels.tasks.cron_utils import validate_run_at_future
        return validate_run_at_future(*args, **kwargs)


def _new_task_id() -> str:
    import uuid
    return f"task_{uuid.uuid4().hex}"

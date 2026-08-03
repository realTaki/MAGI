"""BUS-owned durable task storage and scheduling validation."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import func, select

from magi.bus.contracts.task import (
    TaskExecution,
    TaskFullView,
    TaskPresetView,
    TaskRunView,
    TaskScheduleView,
)
from magi.bus.contracts.session import new_session_id


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


def _full_view(row) -> TaskFullView:
    """Render the operator-facing :class:`TaskFullView` (all columns surfaced in the WebUI)."""
    return TaskFullView(
        id=str(row.id), name=str(row.name), prompt=str(row.prompt),
        cron=str(row.cron), run_at=row.run_at, delivery_to=row.delivery_to,
        tz=str(row.tz), target_channel=str(row.target_channel), uid=int(row.uid),
        enabled=bool(row.enabled), consecutive_failures=int(row.consecutive_failures or 0),
        last_run_at=row.last_run_at, last_status=row.last_status, last_error=row.last_error,
        created_at=str(row.created_at), updated_at=str(row.updated_at),
        session_id=row.session_id, preset_id=row.preset_id, preset_key=row.preset_key,
    )


def _run_view(row) -> TaskRunView:
    return TaskRunView(
        id=str(row.id), task_id=str(row.task_id), session_id=row.session_id,
        trigger=str(row.trigger), started_at=str(row.started_at),
        finished_at=row.finished_at, latency_ms=row.latency_ms,
        status=str(row.status), error=row.error, reply_excerpt=row.reply_excerpt,
        input_tokens=int(row.input_tokens or 0), output_tokens=int(row.output_tokens or 0),
    )


class TaskService:
    """Task persistence façade; channel schedulers observe committed rows."""

    def __init__(self, state_dir: str) -> None:
        self._state_dir = state_dir

    # -- read -----------------------------------------------------------

    def get_schedule(self, task_id: str) -> TaskScheduleView | None:
        from magi.bus.db import open_session
        from magi.bus.models.local.task import Task
        with open_session(self._state_dir) as session:
            row = session.get(Task, str(task_id))
            return _schedule_view(row) if row is not None else None

    def get_schedule_for_name(self, name: str) -> TaskScheduleView | None:
        """Look up a task by its operator-facing ``name`` (for uniqueness pre-checks)."""
        from magi.bus.db import open_session
        from magi.bus.models.local.task import Task
        with open_session(self._state_dir) as session:
            row = session.scalar(select(Task).where(Task.name == name).limit(1))
            return _schedule_view(row) if row is not None else None

    def list_enabled_schedules(self) -> list[TaskScheduleView]:
        from magi.bus.db import open_session
        from magi.bus.models.local.task import Task
        with open_session(self._state_dir) as session:
            rows = session.scalars(
                select(Task).where(Task.enabled.is_(True))
            ).all()
            return [_schedule_view(row) for row in rows]

    def get(self, task_id: str) -> TaskFullView | None:
        """Full task view (all columns) — operator-facing CRUD detail."""
        from magi.bus.db import open_session
        from magi.bus.models.local.task import Task
        with open_session(self._state_dir) as session:
            row = session.get(Task, str(task_id))
            return _full_view(row) if row is not None else None

    def list(
        self,
        *,
        enabled: Optional[bool] = None,
        uid: Optional[int] = None,
        kind: Optional[str] = None,  # "preset" | "custom" | None
    ) -> list[TaskFullView]:
        """List tasks with optional filters. ``kind`` drives the preset-vs-custom split."""
        from magi.bus.db import open_session
        from magi.bus.models.local.task import Task
        with open_session(self._state_dir) as session:
            q = select(Task).order_by(Task.created_at.desc())
            if enabled is not None:
                q = q.where(Task.enabled == (1 if enabled else 0))
            if uid is not None:
                q = q.where(Task.uid == uid)
            if kind == "preset":
                q = q.where(Task.preset_key.is_not(None))
            elif kind == "custom":
                q = q.where(Task.preset_key.is_(None))
            rows = session.scalars(q).all()
            return [_full_view(row) for row in rows]

    def list_runs(self, task_id: str, *, limit: int = 20) -> list[TaskRunView]:
        """Most-recent-first runs for one task."""
        from magi.bus.db import open_session
        from magi.bus.models.local.task import TaskRun
        with open_session(self._state_dir) as session:
            rows = session.scalars(
                select(TaskRun)
                .where(TaskRun.task_id == task_id)
                .order_by(TaskRun.started_at.desc())
                .limit(limit)
            ).all()
            return [_run_view(row) for row in rows]

    # -- preset templates ----------------------------------------------

    def list_presets(self) -> list[TaskPresetView]:
        from magi.bus.models.local.task_preset import TaskPreset
        from magi.bus.db import open_session

        with open_session(self._state_dir) as session:
            return [_preset_view(row) for row in session.scalars(
                select(TaskPreset).order_by(TaskPreset.key)
            )]

    def get_preset(self, preset_id: str) -> TaskPresetView | None:
        from magi.bus.models.local.task_preset import TaskPreset
        from magi.bus.db import open_session

        with open_session(self._state_dir) as session:
            row = session.get(TaskPreset, preset_id)
            return _preset_view(row) if row is not None else None

    def create_preset(self, **values) -> TaskPresetView | None:
        from magi.bus.models.local.task_preset import TaskPreset
        from magi.bus.contracts.session import new_session_id
        from magi.bus.db import open_session

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
        from magi.bus.db import open_session

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
        from magi.bus.db import open_session

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
        from magi.bus.db import open_session
        from magi.bus.models.local.task import Task
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

    def create_task(
        self,
        *,
        task_id: Optional[str] = None,
        name: str,
        prompt: str,
        cron: str,
        run_at: Optional[str],
        delivery_to: Optional[str],
        target_channel: str,
        uid: int,
        session_id: Optional[str] = None,
        tz: str = "UTC",
        enabled: int = 1,
        preset_id: Optional[str] = None,
        preset_key: Optional[str] = None,
    ) -> TaskFullView:
        """Insert a fresh task row. Raises ``ValueError`` on a name collision.

        Used by the WebUI ``POST /api/tasks`` route. Returns the full
        view of the freshly-created task so the caller can serialise
        it directly. When ``task_id`` is omitted, a new ULID is
        generated.
        """
        from magi.bus.db import open_session
        from magi.bus.models.local.task import Task
        new_id = task_id or _new_task_id()
        now = datetime.utcnow().isoformat()
        with open_session(self._state_dir) as session:
            existing = session.scalar(select(Task.id).where(Task.name == name))
            if existing is not None:
                raise ValueError(f"task name {name!r} already exists")
            row = Task(
                id=new_id, name=name, prompt=prompt,
                cron=cron, run_at=run_at, delivery_to=delivery_to,
                target_channel=target_channel, uid=uid,
                session_id=session_id, tz=tz,
                enabled=enabled, consecutive_failures=0,
                preset_id=preset_id, preset_key=preset_key,
                created_at=now, updated_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _full_view(row)

    def update_task(
        self,
        task_id: str,
        *,
        name: Optional[str] = None,
        prompt: Optional[str] = None,
        cron: Optional[str] = None,
        run_at: Optional[str] = None,
        delivery_to: Optional[str] = None,
        target_channel: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> TaskFullView | None:
        """Partial update — each ``None`` means "leave unchanged".

        The :class:`Task` ``enabled`` column is an Integer 0/1; the
        helper normalises the bool input. ``name`` uniqueness is NOT
        enforced here — the API route owns that pre-condition.
        """
        from magi.bus.db import open_session
        from magi.bus.models.local.task import Task
        with open_session(self._state_dir) as session:
            row = session.get(Task, task_id)
            if row is None:
                return None
            if name is not None:
                row.name = name
            if prompt is not None:
                row.prompt = prompt
            if cron is not None:
                row.cron = cron
            if run_at is not None:
                row.run_at = run_at
            if delivery_to is not None:
                row.delivery_to = delivery_to
            if target_channel is not None:
                row.target_channel = target_channel
            if enabled is not None:
                row.enabled = 1 if enabled else 0
            row.tz = _resolve_system_tz(self._state_dir)
            row.updated_at = datetime.utcnow().isoformat()
            session.commit()
            session.refresh(row)
            return _full_view(row)

    def delete_task(self, task_id: str) -> bool:
        from magi.bus.db import open_session
        from magi.bus.models.local.task import Task
        with open_session(self._state_dir) as session:
            row = session.get(Task, task_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def seed_presets_for_contact(self, contact_id: int) -> int:
        """Trigger the preset-seed pass for one contact.

        Wraps ``magi.proactive.task_presets.seed_presets_for_contact``
        in a bus-managed session so the WebUI route can fire the
        seed hook after a contact is created / promoted without
        crossing back to the db layer. Idempotent; returns the
        number of NEW task rows inserted (``0`` on a no-op).
        """
        from magi.bus.db import open_session
        from magi.proactive.task_presets import seed_presets_for_contact as _seed
        with open_session(self._state_dir) as session:
            inserted = _seed(session, contact_id)
            session.commit()
        return inserted

    def create_task_run(
        self,
        *,
        task_id: str,
        run_id: str,
        trigger: str,
        started_at: str,
        session_id: Optional[str] = None,
    ) -> str:
        """Insert a ``TaskRun`` row and return the ``run_id``.

        Used by the manual ``POST /api/tasks/{id}/run`` route so the
        response carries a stable id for the operator's follow-up.
        """
        from magi.bus.db import open_session
        from magi.bus.models.local.task import TaskRun
        with open_session(self._state_dir) as session:
            session.add(TaskRun(
                id=run_id, task_id=task_id, session_id=session_id,
                trigger=trigger, started_at=started_at, status="running",
            ))
            session.commit()
        return run_id

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
        from magi.bus.db import open_session

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
        from magi.bus.db import open_session

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
        from magi.bus.db import open_session
        from magi.bus.db.settings import state_get

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


def _resolve_system_tz(state_dir: str) -> str:
    """Read the configured system timezone (with UTC fallback)."""
    from magi.bus.db.settings import state_get
    raw = state_get(state_dir, "system.timezone")
    return raw if raw else "UTC"
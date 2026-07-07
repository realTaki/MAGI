"""``/api/tasks`` — operator-facing CRUD + manual trigger.

Surface
-------

- ``GET    /api/tasks``                 list (optionally filtered)
- ``GET    /api/tasks/{id}``            single
- ``POST   /api/tasks``                 create
- ``PATCH  /api/tasks/{id}``            partial update
- ``DELETE /api/tasks/{id}``            remove
- ``POST   /api/tasks/{id}/run``        fire-now
- ``GET    /api/tasks/{id}/runs``       history

Auth
----
Same ``AdminGate`` every other Adam endpoint uses
(``magi.channels.webui.api.departments.admin_gate``). The
operator must be a signed-in admin employee; the
``_admin_employee_id`` helper from
:meth:`magi.channels.webui.api.chat_sessions` resolves
the cookie → employee row.

Scheduler integration
---------------------
After every successful DB commit, the endpoint nudges
the module-singleton :class:`TaskScheduler`: a
``register(task)`` on create / update-enable, an
``unregister(task.id)`` on update-disable / delete.
The nudge is wrapped in try/except so a scheduler
glitch never fails the user-visible request — the DB
row is the source of truth, the scheduler is a
warm cache. When the scheduler isn't running
(development, single-container pre-node), the row is
still created/updated; the next node start picks it up
via ``_rehydrate_from_db``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from magi.channels.webui.api.chat_sessions import _admin_employee_id
from magi.channels.webui.api.departments import AdminGate, get_session
from magi.channels.webui.api.errors import MagiHTTPException
from magi.runtime.proactive.cron_utils import validate_cron
from magi.runtime.proactive.orm_models import Task, TaskRun
from magi.runtime.proactive.scheduler import get_scheduler
from magi.runtime.sessions import new_session_id
from magi.runtime.state.orm import Employee

logger = logging.getLogger("magi.channels.webui.api.tasks")

router = APIRouter(tags=["tasks"])


# ──────────────────────────────────────────────────────────────────────── #
# Pydantic shapes
# ──────────────────────────────────────────────────────────────────────── #


class TaskIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=8000)
    cron: str = Field(min_length=1, max_length=120)
    tz: str = "UTC"
    channel: str = Field(default="webui", pattern=r"^(webui|tg)$")

    @field_validator("cron")
    @classmethod
    def _v_cron(cls, v: str) -> str:
        try:
            validate_cron(v)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return v

    @field_validator("tz")
    @classmethod
    def _v_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {v!r}") from exc
        return v


class TaskPatch(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = None
    cron: Optional[str] = None
    tz: Optional[str] = None
    channel: Optional[str] = None
    enabled: Optional[bool] = None

    @field_validator("cron")
    @classmethod
    def _v_cron(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        try:
            validate_cron(v)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return v

    @field_validator("tz")
    @classmethod
    def _v_tz(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {v!r}") from exc
        return v

    @field_validator("channel")
    @classmethod
    def _v_channel(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in ("webui", "tg"):
            raise ValueError("channel must be 'webui' or 'tg'")
        return v


class TaskOut(BaseModel):
    id: str
    name: str
    prompt: str
    cron: str
    tz: str
    channel: str
    employee_id: int
    enabled: bool
    consecutive_failures: int
    last_run_at: Optional[str] = None
    last_status: Optional[str] = None
    last_error: Optional[str] = None
    created_at: str
    updated_at: str


class TaskRunOut(BaseModel):
    id: str
    task_id: str
    session_id: Optional[str] = None
    trigger: str
    started_at: str
    finished_at: Optional[str] = None
    latency_ms: Optional[int] = None
    status: str
    error: Optional[str] = None
    reply_excerpt: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0


class RunResponse(BaseModel):
    run_id: str


# ──────────────────────────────────────────────────────────────────────── #
# ORM → Pydantic
# ──────────────────────────────────────────────────────────────────────── #


def _task_to_out(t: Task) -> TaskOut:
    return TaskOut(
        id=t.id,
        name=t.name,
        prompt=t.prompt,
        cron=t.cron,
        tz=t.tz,
        channel=t.channel,
        employee_id=t.employee_id,
        enabled=bool(t.enabled),
        consecutive_failures=t.consecutive_failures,
        last_run_at=t.last_run_at,
        last_status=t.last_status,
        last_error=t.last_error,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


def _run_to_out(r: TaskRun) -> TaskRunOut:
    return TaskRunOut(
        id=r.id, task_id=r.task_id, session_id=r.session_id,
        trigger=r.trigger, started_at=r.started_at,
        finished_at=r.finished_at, latency_ms=r.latency_ms,
        status=r.status, error=r.error,
        reply_excerpt=r.reply_excerpt,
        input_tokens=r.input_tokens,
        output_tokens=r.output_tokens,
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ──────────────────────────────────────────────────────────────────────── #
# Routes
# ──────────────────────────────────────────────────────────────────────── #


@router.get("/tasks", response_model=List[TaskOut])
def list_tasks(
    request: Request,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
    enabled: Optional[bool] = None,
    employee_id: Optional[int] = None,
) -> list[TaskOut]:
    """List tasks. v0: all tasks visible to the admin.

    ``enabled`` filters by the column; ``employee_id``
    scopes to one owner (admin can still see every
    employee's tasks — useful for the audit pane).
    """
    q = session.query(Task).order_by(Task.created_at.desc())
    if enabled is not None:
        q = q.filter(Task.enabled == (1 if enabled else 0))
    if employee_id is not None:
        q = q.filter(Task.employee_id == employee_id)
    return [_task_to_out(t) for t in q.all()]


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(
    request: Request,
    task_id: str,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> TaskOut:
    t = session.get(Task, task_id)
    if t is None:
        raise MagiHTTPException(
            status_code=404, code="not_found.task",
            detail=f"task {task_id} not found",
        )
    return _task_to_out(t)


@router.post("/tasks", response_model=TaskOut, status_code=201)
def create_task(
    request: Request,
    payload: TaskIn,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> TaskOut:
    """Create a new task.

    ``employee_id`` defaults to the calling admin (the
    common case). An optional ``X-Employee-Id`` header
    lets an admin schedule on behalf of another employee
    (kept for future operator consoles — not surfaced in
    the WebUI today).
    """
    operator_id = _resolve_creator_id(request, payload, session)
    existing = (
        session.query(Task).filter(Task.name == payload.name).one_or_none()
    )
    if existing is not None:
        raise MagiHTTPException(
            status_code=409,
            code="task.name_conflict",
            detail=f"a task with name {payload.name!r} already exists",
        )
    task_id = new_session_id()
    now = _now_iso()
    t = Task(
        id=task_id,
        name=payload.name,
        prompt=payload.prompt,
        cron=payload.cron,
        tz=payload.tz,
        channel=payload.channel,
        employee_id=operator_id,
        enabled=1,
        consecutive_failures=0,
        created_at=now,
        updated_at=now,
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    _register_with_scheduler(t)
    return _task_to_out(t)


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(
    request: Request,
    task_id: str,
    payload: TaskPatch,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> TaskOut:
    t = session.get(Task, task_id)
    if t is None:
        raise MagiHTTPException(
            status_code=404, code="not_found.task",
            detail=f"task {task_id} not found",
        )
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(t, k, v)
    if "enabled" in data:
        t.enabled = 1 if t.enabled else 0
    t.updated_at = _now_iso()
    session.commit()
    session.refresh(t)
    _register_with_scheduler(t)
    return _task_to_out(t)


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(
    request: Request,
    task_id: str,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> None:
    t = session.get(Task, task_id)
    if t is None:
        raise MagiHTTPException(
            status_code=404, code="not_found.task",
            detail=f"task {task_id} not found",
        )
    # Unregister first — see plan §11.5 (scheduler job
    # could tick in the next second). The in-flight
    # fire, if any, re-reads the DB inside ``execute_task``
    # and short-circuits on ``task is None``.
    _unregister_from_scheduler(task_id)
    session.delete(t)
    session.commit()
    return None


@router.post("/tasks/{task_id}/run", response_model=RunResponse)
def run_task_now(
    request: Request,
    task_id: str,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> RunResponse:
    """Fire the task immediately, bypassing cron.

    Pre-creates the ``TaskRun`` row first so the API
    response carries a stable ``run_id`` for the
    operator's follow-up. The scheduler (or the
    synchronous fallback below) updates the row to
    ``success`` / ``failed`` once the runner completes.
    """
    t = session.get(Task, task_id)
    if t is None:
        raise MagiHTTPException(
            status_code=404, code="not_found.task",
            detail=f"task {task_id} not found",
        )
    if not t.enabled:
        raise MagiHTTPException(
            status_code=409, code="task.disabled",
            detail="task is disabled; re-enable before manually firing",
        )
    run_id = new_session_id()
    run = TaskRun(
        id=run_id, task_id=task_id,
        session_id=None,
        trigger="manual",
        started_at=_now_iso(),
        status="running",
    )
    session.add(run)
    session.commit()
    try:
        get_scheduler().submit_now(task_id, run_id=run_id)
    except RuntimeError:
        # Scheduler not running — dev-only sync
        # fallback so the button still works in
        # ``pytest`` mode. The runner writes the row's
        # terminal state directly.
        from magi.runtime.proactive.runner import execute_task
        import asyncio
        try:
            asyncio.run(execute_task(
                _state_dir(), task_id,
                manual=True, pre_created_run_id=run_id,
            ))
        except Exception as exc:  # noqa: BLE001
            logger.exception("sync fallback runner failed: %s", exc)
    return RunResponse(run_id=run_id)


@router.get("/tasks/{task_id}/runs", response_model=List[TaskRunOut])
def list_task_runs(
    request: Request,
    task_id: str,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
    limit: int = Query(20, ge=1, le=100),
) -> list[TaskRunOut]:
    rows = (
        session.query(TaskRun)
        .filter(TaskRun.task_id == task_id)
        .order_by(TaskRun.started_at.desc())
        .limit(limit)
        .all()
    )
    return [_run_to_out(r) for r in rows]


# ──────────────────────────────────────────────────────────────────────── #
# Helpers
# ──────────────────────────────────────────────────────────────────────── #


def _resolve_creator_id(request: Request, _payload: TaskIn, session: Session) -> int:
    """Decide the owner of the new task.

    Resolution order:
      1. ``X-Employee-Id`` header (explicit).
      2. Fall back to the cookie's admin employee id
         via :func:`_admin_employee_id` (chat_sessions'
         helper, which opens its own session).

    v0 keeps both branches simple; future "operator
    console" UIs pass X-Employee-Id.
    """
    raw = request.headers.get("X-Employee-Id")
    if raw is not None and raw.strip():
        try:
            cand = int(raw)
        except ValueError as exc:
            raise MagiHTTPException(
                status_code=400, code="validation.employee_id",
                detail="X-Employee-Id must be an integer",
            ) from exc
        if session.get(Employee, cand) is None:
            raise MagiHTTPException(
                status_code=404, code="not_found.employee",
                detail=f"employee {cand} not found",
            )
        return cand
    # ``_admin_employee_id`` needs a ``SessionStore`` (not a Session);
    # we don't have one in scope here, so call its helper path
    # inline: read the cookie, look up the employee row in the
    # same SQL session the request already has.
    from magi.channels.webui.api.chat_sessions import _resolve_chat_id
    chat_id = _resolve_chat_id(request)
    from magi.runtime.state.orm import open_session as _open
    with _open() as s:
        emp = s.scalar(select(Employee).where(Employee.telegram_id == chat_id))
    if emp is None or emp.role != "admin":
        raise MagiHTTPException(
            status_code=401, code="chat.unknown_sender",
            detail="no admin employee row bound to this chat_id",
        )
    return emp.id


def _register_with_scheduler(task: Task) -> None:
    """Best-effort nudge. Swallow + log on failure."""
    try:
        get_scheduler().register(task)
    except RuntimeError:
        logger.info(
            "scheduler not running yet; task %s will activate on next start",
            task.id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "scheduler.register(%s) failed (DB row is still authoritative): %s",
            task.id, exc,
        )


def _unregister_from_scheduler(task_id: str) -> None:
    try:
        get_scheduler().unregister(task_id)
    except RuntimeError:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("scheduler.unregister(%s) failed: %s", task_id, exc)


def _state_dir() -> str:
    import os
    return os.environ.get("MAGI_STATE_DIR", "/workspace/memories")

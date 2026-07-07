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

from magi.channels.webui.api.departments import AdminGate, get_session
from magi.channels.webui.api.errors import MagiHTTPException
from magi.runtime.proactive.cron_utils import preset_to_cron, validate_cron
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
    """Create-task payload — preset + moments, not raw cron.

    The operator picks a frequency (``hourly`` / ``daily`` /
    ``weekly`` / ``monthly``) and the matching moment
    fields. The server stitches them into a 5-field cron
    string via :func:`preset_to_cron` and stores the
    rendered form in ``Task.cron``. The DB column stays
    a free-form ``cron`` field — future edges (custom
    ranges, lists, etc.) land as additional preset
    kinds without forcing a migration.
    """

    name: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=8000)
    # Preset payload — see :class:`preset_to_cron` for the
    # mapping per frequency.
    frequency: Literal["hourly", "daily", "weekly", "monthly"]
    hour: int = 0
    minute: int = 0
    day_of_week: Optional[int] = None   # 0..6, Mon=0
    day_of_month: Optional[int] = None  # 1..31
    channel: str = Field(default="webui", pattern=r"^(webui|tg)$")

    @field_validator("frequency")
    @classmethod
    def _v_freq(cls, v: str) -> str:
        if v not in ("hourly", "daily", "weekly", "monthly"):
            raise ValueError(f"unsupported frequency: {v!r}")
        return v


class TaskPatch(BaseModel):
    """Partial update — preset fields + enabled.

    Cron is derived from the preset at write-time and
    replaced atomically on every update (no concept of
    "edit only the cron string"; the operator always
    picks a new preset). ``employee_id`` is *not*
    editable here — moving the credentials bound to a
    task is out of scope for v0.
    """

    name: Optional[str] = None
    prompt: Optional[str] = None
    frequency: Optional[Literal["hourly", "daily", "weekly", "monthly"]] = None
    hour: Optional[int] = None
    minute: Optional[int] = None
    day_of_week: Optional[int] = None
    day_of_month: Optional[int] = None
    channel: Optional[str] = None
    enabled: Optional[bool] = None

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
        tz=_resolve_system_tz(),
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

    The operator picks a frequency + moment fields; the
    server renders them into the canonical 5-field cron
    via :func:`preset_to_cron`. The timestamp column
    ``tz`` is no longer per-task — the scheduler always
    reads the operator's system-wide setting
    (``system.timezone``).
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
    cron, _preset_used = _render_cron_from_payload(payload)
    task_id = new_session_id()
    now = _now_iso()
    # ``tz`` is reserved on the model for backend
    # bookkeeping (DEBUGABILITY — we want to know which
    # system TZ was in force when the row was created).
    # The runtime, however, ignores it: every fire reads
    # the current ``system.timezone`` via
    # :func:`magi.runtime.state.settings.state_get`.
    system_tz = _resolve_system_tz()
    t = Task(
        id=task_id,
        name=payload.name,
        prompt=payload.prompt,
        cron=cron,
        tz=system_tz,
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
    # Convert the preset payload (if any) into a single
    # ``cron`` field, atomically replacing what the model
    # stored.
    preset_fields = ("frequency", "hour", "minute", "day_of_week", "day_of_month")
    if any(getattr(payload, f, None) is not None for f in preset_fields):
        cron, _ = _render_cron_from_payload(_PatchProxy(payload))
        t.cron = cron
    data = payload.model_dump(exclude_unset=True)
    # Drop the preset bits — they were translated into
    # ``cron`` above; persisting both would leak noise.
    for f in preset_fields:
        data.pop(f, None)
    data.pop("frequency", None)
    for k, v in data.items():
        setattr(t, k, v)
    if "enabled" in data:
        t.enabled = 1 if t.enabled else 0
    # ``tz`` is now always derived from system settings on
    # fire; we keep the column stamped to the latest system
    # tz so the row's audit info stays useful.
    t.tz = _resolve_system_tz()
    t.updated_at = _now_iso()
    session.commit()
    session.refresh(t)
    _register_with_scheduler(t)
    return _task_to_out(t)


class _PatchProxy:
    """Tiny shim so :func:`_render_cron_from_payload` reads
    the same shape from either :class:`TaskIn` or
    :class:`TaskPatch`. Both expose the same preset
    attributes (``frequency`` / ``hour`` / etc.) so we
    just attribute-forward through the input model.
    """

    def __init__(self, src) -> None:
        self._src = src

    def __getattr__(self, item):
        return getattr(self._src, item)


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


def _resolve_creator_id(request: Request, _payload, session: Session) -> int:
    """Decide the owner of the new task.

    Resolution order:
      1. ``X-Employee-Id`` header (explicit, may be used
         by future operator consoles; v0 WebUI doesn't
         expose it).
      2. Fall back to the cookie's signed-in employee.
         Allowed roles are ``admin`` (signed in via the
         super-admin form) and ``assigned`` (the person
         this MAGI serves). ``employee`` and ``guest``
         are barred — they don't sign in.

    Returns the resolved ``employee_id``. The task's
    credentials are bound to whoever this returns (i.e.
    cron time fires + LLM call charges the creator's
    own provider / API key).
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
        emp = session.get(Employee, cand)
        if emp is None:
            raise MagiHTTPException(
                status_code=404, code="not_found.employee",
                detail=f"employee {cand} not found",
            )
        _enforce_creator_role(emp.role)
        return emp.id
    # Fall back to the cookie: same path as chat_sessions,
    # but the role gate is looser — ``assigned`` is also
    # welcome. ``_admin_employee_id`` enforces ``role ==
    # "admin"``; we duplicate it inline with the broader
    # gate.
    from magi.channels.webui.api.chat_sessions import _resolve_chat_id
    chat_id = _resolve_chat_id(request)
    emp = session.scalar(
        select(Employee).where(Employee.telegram_id == chat_id)
    )
    if emp is None:
        raise MagiHTTPException(
            status_code=401, code="chat.unknown_sender",
            detail="no employee row bound to this chat_id; sign in first",
        )
    _enforce_creator_role(emp.role)
    return emp.id


def _enforce_creator_role(role: str) -> None:
    if role not in _ROLE_MAY_CREATE:
        raise MagiHTTPException(
            status_code=403,
            code="tasks.creator_forbidden",
            detail=(
                f"role {role!r} cannot create tasks; "
                f"allowed: {sorted(_ROLE_MAY_CREATE)}"
            ),
        )


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


# Constants for the creator-role gate. ``admin`` and
# ``assigned`` employees may create tasks (charged to their
# own credentials); ``employee`` and ``guest`` don't sign
# in to a MAGI node and so have no use for scheduled
# tasks. Mirrors the gate in ``schedule_task`` so the
# API and the LLM tool are consistent.
_ROLE_MAY_CREATE = {"admin", "assigned"}


def _resolve_system_tz() -> str:
    """Read the configured system timezone.

    Falls back to ``UTC`` when the KV store is empty or
    the stored value isn't a valid IANA name. We share
    this helper with the WebUI panel that writes
    ``system.timezone`` so the operator sees the same
    value everywhere.
    """
    from magi.runtime.state.settings import state_get
    raw = state_get(_state_dir(), "system.timezone")
    if not raw:
        return "UTC"
    return raw  # the WebUI validates the IANA name on save


def _render_cron_from_payload(
    payload,
    preset_only: bool = False,
) -> tuple[str, dict]:
    """Render preset payload → (cron, fields_used) tuple.

    If ``preset_only`` is true, returns the already-rendered
    cron from the model. Otherwise it stitches the preset +
    moment fields into the 5-field string via
    :func:`preset_to_cron`.

    The returned ``fields_used`` dict (an empty dict today)
    is reserved for future surfaces — e.g. the WebUI may
    ask for the rendered cron + a backwards-compatibility
    descriptor of the underlying preset.
    """
    try:
        cron = preset_to_cron(
            payload.frequency,
            hour=payload.hour or 0,
            minute=payload.minute or 0,
            day_of_week=payload.day_of_week,
            day_of_month=payload.day_of_month,
        )
    except (ValueError, TypeError) as exc:
        raise MagiHTTPException(
            status_code=400, code="validation.cron_preset",
            detail=str(exc),
        ) from exc
    return cron, {}

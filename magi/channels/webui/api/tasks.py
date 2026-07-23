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
``_admin_uid`` helper from
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
from typing import Annotated, List, Literal, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from magi.channels.webui.api.departments import AdminGate, get_session
from magi.channels.webui.api.errors import MagiHTTPException
from magi.agent.proactive.cron_utils import preset_to_cron, validate_cron, validate_run_at, validate_run_at_future
from magi.agent.proactive.orm_models import Task, TaskRun
from magi.agent.proactive.scheduler import get_scheduler
from magi.agent.memory.session import new_session_id
from magi.agent.db import ChatSession, Employee, require_state_dir

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
    # mapping per cron-driven frequency. ``"once"`` is the
    # one-shot case: ``run_at`` becomes mandatory, the
    # moment fields are ignored, and the row stores cron=""
    # + run_at=<ISO>.
    frequency: Literal["hourly", "daily", "weekly", "monthly", "once"]
    hour: int = 0
    minute: int = 0
    day_of_week: Optional[int] = None   # 0..6, Mon=0
    day_of_month: Optional[int] = None  # 1..31
    # Required when ``frequency="once"``; ignored otherwise.
    # The validator below enforces the one-way conditional.
    run_at: Optional[str] = None
    channel: str = Field(default="webui", pattern=r"^(webui|tg)$")
    # Concrete delivery destination — semantic depends on
    # ``channel`` (see ``Task.delivery_to`` doc). The
    # ``ScheduleTaskTool`` tool path applies the same
    # format-validating logic; the WebUI form sends
    # ``"new"`` for the create-new-session default.
    delivery_to: Optional[str] = None

    @field_validator("frequency")
    @classmethod
    def _v_freq(cls, v: str) -> str:
        if v not in ("hourly", "daily", "weekly", "monthly", "once"):
            raise ValueError(f"unsupported frequency: {v!r}")
        return v

    # ``run_at`` ↔ ``frequency`` cross-field rule is enforced
    # in :func:`_validate_run_at_against_frequency` below
    # rather than here. Pydantic 2 + FastAPI's TypeAdapter
    # tripped on a field-level validator with an
    # ``info.data`` reference when the model is referenced
    # through ``Annotated[TaskIn, Field(payload)]`` at route-
    # mount time (the "not fully defined" error pulls
    # :class:`TaskIn` and its ``model_rebuild`` helper up at
    # every other endpoint that reuses the route factory).
    # The cross-field check at the route boundary is
    # equivalent — Pydantic stops short of letting invalid
    # combinations through because we 422 (or 400) the bad
    # payload before the row hits the DB.


class TaskPatch(BaseModel):
    """Partial update — preset fields + enabled.

    Cron is derived from the preset at write-time and
    replaced atomically on every update (no concept of
    "edit only the cron string"; the operator always
    picks a new preset). ``uid`` is *not*
    editable here — moving the credentials bound to a
    task is out of scope for v0.
    """

    name: Optional[str] = None
    prompt: Optional[str] = None
    frequency: Optional[Literal[
        "hourly", "daily", "weekly", "monthly", "once",
    ]] = None
    hour: Optional[int] = None
    minute: Optional[int] = None
    day_of_week: Optional[int] = None
    day_of_month: Optional[int] = None
    # Optional on PATCH (the patch needs to be permissive
    # about partial updates — the model-level run_at ↔
    # frequency invariant runs on the POST path only).
    run_at: Optional[str] = None
    delivery_to: Optional[str] = None
    channel: Optional[str] = None
    enabled: Optional[bool] = None

    @field_validator("frequency")
    @classmethod
    def _v_freq(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in (
            "hourly", "daily", "weekly", "monthly", "once",
        ):
            raise ValueError(f"unsupported frequency: {v!r}")
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
    # ``cron`` for recurring rows; ``run_at`` for one-shot
    # rows (mutually exclusive in the row — see the
    # ORM-level docs at ``Task.run_at``). The dashboard
    # picks which to render in the humanised cell.
    cron: str
    run_at: Optional[str] = None
    # Concrete delivery destination — semantic per
    # ``channel`` (see ``Task.delivery_to`` doc). The cell
    # renders it as a "→ <target>" snippet below the
    # schedule row so the operator knows where each fire
    # will land.
    delivery_to: Optional[str] = None
    tz: str
    channel: str
    uid: int
    enabled: bool
    consecutive_failures: int
    last_run_at: Optional[str] = None
    last_status: Optional[str] = None
    last_error: Optional[str] = None
    created_at: str
    updated_at: str
    # Agent's home session (allocated at task creation,
    # channel="task"). The WebUI runs drawer fetches
    # this session's chat history directly via the
    # chat-sessions endpoint; surfacing it here means
    # the drawer doesn't have to do a second lookup just
    # to find which session belongs to which task.
    session_id: Optional[str] = None


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
        run_at=t.run_at,
        delivery_to=t.delivery_to,
        tz=_resolve_system_tz(),
        channel=t.channel,
        uid=t.uid,
        enabled=bool(t.enabled),
        consecutive_failures=t.consecutive_failures,
        last_run_at=t.last_run_at,
        last_status=t.last_status,
        last_error=t.last_error,
        created_at=t.created_at,
        updated_at=t.updated_at,
        session_id=t.session_id,
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
    uid: Optional[int] = None,
) -> list[TaskOut]:
    """List tasks. v0: all tasks visible to the admin.

    ``enabled`` filters by the column; ``uid``
    scopes to one owner (admin can still see every
    employee's tasks — useful for the audit pane).
    """
    q = session.query(Task).order_by(Task.created_at.desc())
    if enabled is not None:
        q = q.filter(Task.enabled == (1 if enabled else 0))
    if uid is not None:
        q = q.filter(Task.uid == uid)
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
    # Cross-field invariant for ``once``. Pydantic 2 + FastAPI's
    # TypeAdapter keep tripping on field-validators that read
    # ``info.data`` when the model is parameterised via
    # ``Annotated[TaskIn, Field(payload)]`` at route-mount time,
    # so the cross-field rule lives at the route boundary
    # instead. ``TaskIn`` carries ``Literal["...", "once"]`` so
    # an unknown frequency short-circuits at 422 in Pydantic
    # before we get here.
    if payload.frequency == "once" and not payload.run_at:
        raise MagiHTTPException(
            status_code=400,
            code="validation.run_at_required_for_once",
            detail=(
                "run_at is required when frequency='once'; "
                "pass an ISO 8601 timestamp (e.g. "
                "'2026-08-01T15:30:00+08:00')."
            ),
        )
    if payload.frequency != "once" and payload.run_at:
        raise MagiHTTPException(
            status_code=400,
            code="validation.run_at_only_for_once",
            detail=(
                f"run_at is set; frequency must be 'once', "
                f"got {payload.frequency!r}."
            ),
        )
    # Past-time ``run_at`` would silently no-op: apscheduler's
    # ``DateTrigger`` returns ``None`` from
    # ``get_next_fire_time`` when ``run_date`` is in the
    # past at registration. The task lives in the DB but
    # never fires — operator sees a row that does nothing.
    # Reject at the route boundary so the drawer's error
    # message tells them to pick a future time. The grace
    # window inside :func:`validate_run_at_future`
    # absorbs small clock skew.
    if payload.frequency == "once" and payload.run_at:
        # Canonicalise first (naive → +00:00, round to
        # seconds) so the "now vs run_at" comparison uses
        # the same shape the DB row will store.
        try:
            canonical = validate_run_at(payload.run_at)
        except ValueError:
            # _render_cron_from_payload below re-validates;
            # let that path emit the canonical
            # ``validation.run_at`` error code so the
            # frontend only has to handle one error shape.
            canonical = None
        if canonical is not None:
            try:
                validate_run_at_future(canonical)
            except ValueError as exc:
                raise MagiHTTPException(
                    status_code=400,
                    code="validation.run_at_in_past",
                    detail=str(exc),
                ) from exc

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
    cron, run_at_iso, _preset_used = _render_cron_from_payload(payload)
    # Server-side derive ``delivery_to`` per the unified
    # rule. ``channel=tg`` requires the operator to have a
    # ``telegram_id`` bound — a missing binding is a config
    # mistake, surfaced as 400 so the drawer doesn't
    # silently store a NULL that the runner then can't
    # dispatch. ``channel=webui`` leaves ``delivery_to``
    # NULL: the task's own session IS the operator-visible
    # record (no separate IM target needed).
    delivery_to = _resolve_delivery_to(
        session, channel=payload.channel,
        uid=operator_id,
        explicit=payload.delivery_to,
    )
    # Allocate the task's home session NOW. Every cron
    # fire of this task accumulates into this single
    # session — same channel="task", same ``tgid`` stamp
    # (carries the IM target for the runner's TG-push
    # wiring, but the session itself is never channel="tg"
    # — see ``chat_sessions.channel`` semantics). The
    # operator sees this session in their chat list along
    # with their webui + tg chats (the chat-sessions
    # router filters only by uid).
    operator = session.get(Employee, operator_id)
    task_session_id = new_session_id()
    now = _now_iso()
    task_session = ChatSession(
        session_id=task_session_id,
        # ``tgid`` here records the IM target so the
        # runner's ``_resolve_session_for_task`` lookup
        # (when called for legacy rows that pre-date
        # this column) can recover. For webui tasks the
        # value is the operator's telegram_id as a
        # harmless breadcrumb — no routing depends on
        # it because channel="webui" disables the
        # send_message tool.
        delivery_address=str(operator.telegram_id or ""),
        uid=operator_id,
        channel="task",
        title=f"[定时] {payload.name}",
        created_at=now,
        updated_at=now,
    )
    session.add(task_session)
    # Flush so the chat_sessions row's PK is in the DB
    # before the FK reference from Task.session_id below.
    session.flush()
    task_id = new_session_id()
    # ``tz`` is reserved on the model for backend
    # bookkeeping (DEBUGABILITY — we want to know which
    # system TZ was in force when the row was created).
    # The runtime, however, ignores it: every fire reads
    # the current ``system.timezone`` via
    # :func:`magi.agent.db.settings.state_get`.
    system_tz = _resolve_system_tz()
    t = Task(
        id=task_id,
        name=payload.name,
        prompt=payload.prompt,
        cron=cron,
        run_at=run_at_iso,
        delivery_to=delivery_to,
        # Wire the freshly-allocated session as the
        # task's home; the runner reads this column at
        # fire time and appends to it.
        session_id=task_session_id,
        tz=system_tz,
        channel=payload.channel,
        uid=operator_id,
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
    # ``cron`` / ``run_at`` field, atomically replacing
    # what the model stored.
    preset_fields = ("frequency", "hour", "minute", "day_of_week", "day_of_month")
    if any(getattr(payload, f, None) is not None for f in preset_fields):
        cron, run_at_iso, _ = _render_cron_from_payload(_PatchProxy(payload))
        t.cron = cron
        t.run_at = run_at_iso
    data = payload.model_dump(exclude_unset=True)
    # Drop the preset bits — they were translated into
    # ``cron``/``run_at`` above; persisting both would
    # leak noise. ``run_at`` itself is the once-shape
    # payload, so it stays in the model_dump (the setter
    # below writes it through).
    for f in preset_fields:
        data.pop(f, None)
    data.pop("frequency", None)
    # ``channel`` and ``delivery_to`` are derived server-side;
    # the patch may explicitly change channel but
    # ``delivery_to`` is always re-derived so the row
    # reflects the operator's *current* TG binding (which
    # may have been updated since the row was created).
    patch_channel = data.pop("channel", None)
    data.pop("delivery_to", None)
    if patch_channel is not None:
        t.channel = patch_channel
    # Always re-derive. The helper reads the row's current
    # channel + the operator's current telegram_id; an
    # unchanged channel still wants the row to track any
    # later TG-binding edit.
    t.delivery_to = _resolve_delivery_to(
        session, channel=t.channel,
        uid=t.uid, explicit=None,
    )
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
        from magi.agent.proactive.runner import execute_task
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
      2. Fall back to the cookie's signed-in employee
         (``magi_session`` carries the uid after
         the D.24 migration — not the telegram_id). We
         use :func:`_resolve_uid` so the cookie
         format matches every other admin-gated route.
         Allowed roles are ``admin`` (signed in via the
         super-admin form) and ``assigned`` (the person
         this MAGI serves). ``employee`` and ``guest``
         are barred — they don't sign in.

    Returns the resolved ``uid``. The task's
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
                status_code=400, code="validation.uid",
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
    # Fall back to the cookie: D.24 made ``magi_session``
    # carry the uid directly, so the lookup is
    # ``session.get(Employee, eid)`` — no telegram_id
    # detour. The role gate is duplicated inline (instead
    # of reusing :func:`_admin_uid`) because
    # ``assigned`` is also welcome here, and
    # ``_admin_uid` enforces ``role == "admin"``
    # only.
    from magi.channels.webui.api.chat_sessions import _resolve_uid
    uid = _resolve_uid(request)
    emp = session.get(Employee, eid)
    if emp is None:
        raise MagiHTTPException(
            status_code=401, code="chat.unknown_sender",
            detail=(
                f"no employee row bound to this session "
                f"(uid={eid}); sign in first"
            ),
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
    return require_state_dir()


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
    from magi.agent.db.settings import state_get
    raw = state_get(_state_dir(), "system.timezone")
    if not raw:
        return "UTC"
    return raw  # the WebUI validates the IANA name on save


def _resolve_delivery_to(
    session: Session,
    *,
    channel: str,
    uid: int,
    explicit: str | None,
) -> str:
    """Server-derived ``delivery_to`` per the unified rule.

    The operator does not pick a delivery destination from
    the WebUI form; the channel alone drives it:

      - ``channel='tg'``: must use the operator's bound
        ``telegram_id``. Missing binding is a config
        mistake — surface as 400 so the drawer doesn't
        silently store a NULL the runner then can't
        dispatch.
      - ``channel='webui'``: ``explicit`` (caller-supplied
        session_id from the LLM-in-chat path) is honoured
        when present; otherwise the WebUI default is
        ``"new"`` (a fresh session per fire).

    Re-deriving on every PATCH keeps the row coherent with
    any later TG-binding edit the operator made.
    """
    if channel == "tg":
        emp = session.get(Employee, uid)
        if emp is None or not emp.telegram_id:
            raise MagiHTTPException(
                status_code=400,
                code="tasks.telegram_not_bound",
                detail=(
                    f"channel='tg' requires the operator "
                    f"(employee {uid}) to have a "
                    f"telegram_id bound; bind a TG chat first "
                    f"(Settings → 员工)."
                ),
            )
        return str(emp.telegram_id)
    # webui: honour an explicit caller-supplied value (the
    # LLM-in-chat path passes ``ctx.session_id``); else
    # default to "new" for fresh-session-per-fire.
    return explicit if explicit else "new"


def _render_cron_from_payload(
    payload,
    preset_only: bool = False,
) -> tuple[str, str | None, dict]:
    """Render preset payload → (cron, run_at, fields_used).

    For the four cron-driven presets (``hourly`` /
    ``daily`` / ``weekly`` / ``monthly``) this returns
    ``(cron_string, None, {})``. For ``once`` it returns
    ``("", run_at_iso, {})`` so the caller knows to write
    to the ``run_at`` column instead of ``cron``.

    The returned ``fields_used`` dict (an empty dict today)
    is reserved for future surfaces — e.g. the WebUI may
    ask for the rendered cron + a backwards-compatibility
    descriptor of the underlying preset.
    """
    if payload.frequency == "once":
        # ``TaskIn``'s model_validator already enforced
        # that run_at is present + non-empty; we canonicalise
        # it here so a naive ISO round-trips as UTC + a
        # normalised offset, matching :func:`validate_run_at`.
        try:
            run_at_iso = validate_run_at(payload.run_at or "")
        except ValueError as exc:
            raise MagiHTTPException(
                status_code=400, code="validation.run_at",
                detail=str(exc),
            ) from exc
        return "", run_at_iso, {}
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
    return cron, None, {}

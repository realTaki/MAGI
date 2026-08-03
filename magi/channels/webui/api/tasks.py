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
(``magi.channels.webui.api.auth_gates.admin_gate``). The
operator must be a signed-in admin contact; the
``_admin_uid`` helper from
:meth:`magi.channels.webui.api.chat_sessions` resolves
the cookie → contact row.

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

from magi.channels.webui.api.auth_gates import AdminGate
from magi.channels import Channel
from magi.db import get_session
from magi.channels.webui.api.errors import MagiHTTPException
from magi.bus.task_schedule import preset_to_cron, validate_cron, validate_run_at, validate_run_at_future
from magi.bus.models.local.task import Task, TaskRun
from magi.channels.tasks.scheduler import get_scheduler
from magi.bus.contracts.session import new_session_id
from magi.db import ChatSession, Contact, require_state_dir

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
    target_channel: Channel | str = Field(default=Channel.WEBUI, pattern=r"^(webui|tg)$")
    # Concrete delivery destination — semantic depends on
    # ``target_channel`` (see ``Task.delivery_to`` doc). The
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
    target_channel: Optional[str] = None
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

    @field_validator("target_channel")
    @classmethod
    def _v_channel(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in (Channel.WEBUI, Channel.TG):
            raise ValueError("target_channel must be 'webui' or 'tg'")
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
    target_channel: str
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
    # Preset back-pointers (see TaskPreset model +
    # ``magi/proactive/task_presets.py``). Non-null iff
    # the row was auto-seeded from a template; the
    # WebUI's Tasks pane uses ``preset_key IS NULL`` /
    # ``IS NOT NULL`` to split the list into "preset
    # tasks" and "custom tasks". ``preset_key`` stays
    # populated even if the source template is deleted
    # (FK ``SET NULL`` on ``preset_id``), so the UI
    # grouping doesn't silently shift on template
    # removal.
    preset_id: Optional[str] = None
    preset_key: Optional[str] = None


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
        target_channel=t.target_channel,
        uid=t.uid,
        enabled=bool(t.enabled),
        consecutive_failures=t.consecutive_failures,
        last_run_at=t.last_run_at,
        last_status=t.last_status,
        last_error=t.last_error,
        created_at=t.created_at,
        updated_at=t.updated_at,
        session_id=t.session_id,
        preset_id=t.preset_id,
        preset_key=t.preset_key,
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
    kind: Optional[Literal["preset", "custom"]] = Query(
        None,
        description=(
            "Drives the WebUI's preset-vs-custom split. "
            "'preset' returns rows with preset_key IS NOT NULL; "
            "'custom' returns rows with preset_key IS NULL. "
            "Omit to return all rows regardless of source."
        ),
    ),
) -> list[TaskOut]:
    """List tasks. v0: all tasks visible to the admin.

    ``enabled`` filters by the column; ``uid`` scopes to
    one owner (admin can still see every contact's tasks
    — useful for the audit pane); ``kind`` filters by
    preset-derived vs operator-authored.
    """
    q = session.query(Task).order_by(Task.created_at.desc())
    if enabled is not None:
        q = q.filter(Task.enabled == (1 if enabled else 0))
    if uid is not None:
        q = q.filter(Task.uid == uid)
    if kind == "preset":
        q = q.filter(Task.preset_key.is_not(None))
    elif kind == "custom":
        q = q.filter(Task.preset_key.is_(None))
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

    operator_id = _resolve_creator_id(request, payload)
    cron, run_at_iso, _preset_used = _render_cron_from_payload(payload)
    # D.28: resolve the per-channel delivery address via
    # the channel dispatcher BEFORE we open the
    # FastAPI-managed ``session`` transaction. The
    # dispatcher opens its own SQLite session
    # internally — a nested BEGIN IMMEDIATE inside the
    # outer route-scoped txn would deadlock SQLite (the
    # outer txn holds the reserved lock; the inner one
    # can't acquire it). Resolve the address up-front
    # here while the FastAPI session is still in its
    # implicit-begin phase (the ``session.query``
    # below is what triggers the actual BEGIN IMMEDIATE).
    # For ``channel='tg'`` the dispatcher raises a
    # friendly 400 when the operator has no TG binding;
    # for ``channel='webui'`` it returns ``""`` (the
    # session has no IM target — channel="webui"
    # disables outbound send_message).
    from magi.channels import dispatcher as channel_dispatcher
    task_session_delivery_address = (
        channel_dispatcher.lookup_im_id(operator_id, Channel.TG) or ""
    )
    # ``tz`` is reserved on the model for backend
    # bookkeeping (DEBUGABILITY — we want to know which
    # system TZ was in force when the row was created).
    # The runtime, however, ignores it: every fire reads
    # the current ``system.timezone`` via
    # :func:`magi.db.settings.state_get`. Resolve
    # BEFORE we issue any outer-session queries —
    # ``state_get`` opens its own ORM session, and a
    # nested BEGIN IMMEDIATE inside the FastAPI route's
    # ``Depends(get_session)`` transaction would deadlock
    # SQLite (the outer txn holds the reserved lock;
    # the inner ``state_get`` session can't acquire it).
    system_tz = _resolve_system_tz()
    # ``_resolve_delivery_to`` also calls the channel
    # dispatcher internally for ``channel='tg'`` (so it
    # can return a 400 when the operator has no TG
    # binding). Same rationale as the dispatcher call
    # above — resolve BEFORE ``session.query`` begins the
    # outer BEGIN IMMEDIATE.
    delivery_to = _resolve_delivery_to(
        target_channel=payload.target_channel,
        uid=operator_id,
        explicit=payload.delivery_to,
    )
    existing = (
        session.query(Task).filter(Task.name == payload.name).one_or_none()
    )
    if existing is not None:
        raise MagiHTTPException(
            status_code=409,
            code="task.name_conflict",
            detail=f"a task with name {payload.name!r} already exists",
        )
    # Allocate the task's home session NOW. Every cron
    # fire of this task accumulates into this single
    # session — same channel="task", same
    # ``delivery_address`` stamp (carries the IM target
    # for the runner's TG-push wiring, but the session
    # itself is never channel="tg" — see
    # ``chat_sessions.channel`` semantics). The operator
    # sees this session in their chat list along with
    # their webui + tg chats (the chat-sessions router
    # filters only by uid).
    #
    # D.28: the per-channel delivery address is resolved
    # via the channel dispatcher. Looking it up inside
    # this ``open_session()`` would deadlock (the
    # dispatcher's adapter opens its own SQLite session
    # — nested BEGIN IMMEDIATE in the outer txn), so the
    # lookup happens OUTSIDE the with block (above) and
    # is passed in as ``task_session_delivery_address``.
    task_session_id = new_session_id()
    now = _now_iso()
    task_session = ChatSession(
        session_id=task_session_id,
        # The IM target as a breadcrumb so the runner's
        # ``_resolve_session_for_task`` lookup (for
        # legacy rows that pre-date this column) can
        # recover. For webui tasks the value is harmless
        # — no routing depends on it because channel=
        # "webui" disables the send_message tool.
        delivery_address=task_session_delivery_address,
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
        target_channel=payload.target_channel,
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
    patch_target_channel = data.pop("target_channel", None)
    data.pop("delivery_to", None)
    if patch_target_channel is not None:
        t.target_channel = patch_target_channel
    # Always re-derive. The helper reads the row's current
    # channel + the operator's current bound chat id (via
    # the channel dispatcher, D.28); an unchanged channel
    # still wants the row to track any later TG-binding
    # edit. Resolve BEFORE any nested dispatcher call —
    # the FastAPI ``Depends(get_session)`` transaction is
    # already open via ``session.get(Task, ...)`` above;
    # calling ``dispatcher.lookup_im_id`` (inside
    # ``_resolve_delivery_to``) inside that open txn would
    # deadlock SQLite. The cache in :func:`_resolve_system_tz`
    # keeps the second-pass write below cheap too.
    t.delivery_to = _resolve_delivery_to(
        target_channel=t.target_channel,
        uid=t.uid, explicit=None,
    )
    for k, v in data.items():
        setattr(t, k, v)
    if "enabled" in data:
        t.enabled = 1 if t.enabled else 0
    # ``tz`` is now always derived from system settings on
    # fire; we keep the column stamped to the latest system
    # tz so the row's audit info stays useful. Cached
    # globally so the ``state_get`` round-trip happens at
    # most once per process (a route that holds an outer
    # session open can't issue that call inside this
    # handler — it would deadlock on nested BEGIN
    # IMMEDIATE).
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
        from magi.channels.tasks.channel import TaskChannel
        import asyncio
        try:
            asyncio.run(TaskChannel.dispatch(
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


def _resolve_creator_id(request: Request, _payload) -> int:
    """Decide the owner of the new task.

    Resolution order:
      1. ``X-Contact-Id`` header (explicit, may be used
         by future operator consoles; v0 WebUI doesn't
         expose it).
      2. Fall back to the cookie's signed-in contact
         (``magi_session`` carries the uid after
         the D.24 migration — not the telegram_id). We
         use :func:`_resolve_uid` so the cookie
         format matches every other admin-gated route.
         Allowed roles are ``admin`` (signed in via the
         super-admin form) and ``assigned`` (the person
         this MAGI serves). ``contact`` and ``guest``
         are barred — they don't sign in.

    Returns the resolved ``uid``. The task's
    credentials are bound to whoever this returns (i.e.
    cron time fires + LLM call charges the creator's
    own provider / API key).
    """
    raw = request.headers.get("X-Contact-Id")
    if raw is not None and raw.strip():
        try:
            cand = int(raw)
        except ValueError as exc:
            raise MagiHTTPException(
                status_code=400, code="validation.uid",
                detail="X-Contact-Id must be an integer",
            ) from exc
        # Open our own short-lived session so the
        # ``Contact`` lookup doesn't begin a transaction
        # on the route's session (the dispatcher call in
        # :func:`create_task` would deadlock against that
        # outer txn).
        from magi.db import open_session as _open
        with _open() as db:
            contact = db.get(Contact, cand)
        if contact is None:
            raise MagiHTTPException(
                status_code=404, code="not_found.contact",
                detail=f"contact {cand} not found",
            )
        _enforce_creator_can_create(admin=bool(contact.admin), role=contact.role)
        return contact.id
    # Fall back to the cookie: D.24 made ``magi_session``
    # carry the uid directly, so the lookup is
    # ``db.get(Contact, uid)`` — no telegram_id
    # detour. The creator gate passes ``admin=True`` OR
    # ``role='assigned'`` (replaces the pre-2024
    # ``role in {"admin", "assigned"}`` check). Same
    # ``open_session`` rationale as above — we don't want
    # to begin a transaction on the route's session before
    # the dispatcher lookup fires.
    from magi.channels.webui.api.chat_sessions import _resolve_uid
    uid = _resolve_uid(request)
    from magi.db import open_session as _open
    with _open() as db:
        contact = db.get(Contact, uid)
    if contact is None:
        raise MagiHTTPException(
            status_code=401, code="chat.unknown_sender",
            detail=(
                f"no contact row bound to this session "
                f"(uid={uid}); sign in first"
            ),
        )
    _enforce_creator_can_create(admin=bool(contact.admin), role=contact.role)
    return contact.id


def _enforce_creator_can_create(*, admin: bool, role: str) -> None:
    """Gate who can author a scheduled task.

    Accepts the caller if either:
      - ``admin=True`` — WebUI operator (any role).
      - ``role='assigned'`` — the served user, regardless of
        admin status.

    Replaces the pre-2024 ``role in {"admin", "assigned"}``
    check. The old enum-driven gate can't express "the
    served user who is also their own operator" or "a
    colleague with backend access who isn't the served
    user". Both are now legitimate authors.
    """
    if admin or role == "assigned":
        return
    raise MagiHTTPException(
        status_code=403,
        code="tasks.creator_forbidden",
        detail=(
            f"admin={admin}, role={role!r} cannot create tasks; "
            f"required: admin=True or role='assigned'"
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


# The creator-role gate moved from a constant to a helper
# (``_enforce_creator_can_create``) when ``role='admin'``
# was retired in favour of the ``admin`` boolean. The
# helper takes both fields so the LLM tool side
# (``magi/tools/schedule_task.py``) and this API
# agree on the policy: ``admin=True OR role='assigned'``.
# ``contact`` and ``guest`` rows can't sign in to a MAGI
# node and so have no use for scheduled tasks.


def _resolve_system_tz() -> str:
    """Read the configured system timezone.

    Falls back to ``UTC`` when the KV store is empty or
    the stored value isn't a valid IANA name. We share
    this helper with the WebUI panel that writes
    ``system.timezone`` so the operator sees the same
    value everywhere.

    Process-wide cache: a single page render calls this
    3-4 times (once for ``Task.tz`` on create, once in
    ``_task_to_out`` on the response shape, once per list
    item on ``GET /api/tasks``). Each call opens its own
    ORM session via :func:`state_get` — and a FastAPI
    route that holds its ``Depends(get_session)`` session
    open during that nested call would deadlock SQLite
    (BEGIN IMMEDIATE inside the outer txn). Caching the
    result avoids opening a fresh session at all within
    one process. The KV row is rare-write — the
    Settings → timezone card is the only writer — so a
    stale-by-a-minute read is acceptable; the API endpoint
    that writes it also clears the cache.
    """
    global _SYSTEM_TZ_CACHE
    if _SYSTEM_TZ_CACHE is not None:
        return _SYSTEM_TZ_CACHE
    from magi.db.settings import state_get
    raw = state_get(_state_dir(), "system.timezone")
    val = raw if raw else "UTC"
    _SYSTEM_TZ_CACHE = val
    return val


_SYSTEM_TZ_CACHE: str | None = None


def _invalidate_system_tz_cache() -> None:
    """Drop the cached tz so the next ``_resolve_system_tz``
    call re-reads from the KV store. The
    ``PUT /api/system-settings/timezone`` endpoint calls
    this after a successful write.
    """
    global _SYSTEM_TZ_CACHE
    _SYSTEM_TZ_CACHE = None


def _resolve_delivery_to(
    *,
    target_channel: str,
    uid: int,
    explicit: str | None,
) -> str:
    """Server-derived ``delivery_to`` per the unified rule.

    The operator does not pick a delivery destination from
    the WebUI form; the channel alone drives it:

      - ``channel='tg'``: must use the operator's bound
        TG chat (resolved via the channel dispatcher,
        D.28). Missing binding is a config mistake —
        surface as 400 so the drawer doesn't silently
        store a NULL the runner then can't dispatch.
      - ``channel='webui'``: ``explicit`` (caller-supplied
        session_id from the LLM-in-chat path) is honoured
        when present; otherwise the WebUI default is
        ``"new"`` (a fresh session per fire).

    Re-deriving on every PATCH keeps the row coherent with
    any later TG-binding edit the operator made.

    Important: the ``channel='tg'`` branch calls
    ``dispatcher.lookup_im_id``, which opens its own ORM
    session. Callers that hold an outer FastAPI
    ``Depends(get_session)`` transaction must invoke this
    helper BEFORE issuing any outer-session queries —
    a nested BEGIN IMMEDIATE inside the open outer txn
    would deadlock SQLite.
    """
    if target_channel == Channel.TG:
        from magi.channels import dispatcher as channel_dispatcher
        chat_id = channel_dispatcher.lookup_im_id(uid, Channel.TG)
        if not chat_id:
            raise MagiHTTPException(
                status_code=400,
                code="tasks.telegram_not_bound",
                detail=(
                    f"channel='tg' requires the operator "
                    f"(contact {uid}) to have a "
                    f"telegram_id bound; bind a TG chat first "
                    f"(Settings → Contacts)."
                ),
            )
        return chat_id
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

"""Task HTTP API backed directly by the explicit BUS Books and job board."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from magi.old_bus.firmwares.books.local.tasksBook import Task, preset_to_cron, validate_run_at
from magi.old_bus.firmwares.jobs.runTaskJob import RunTaskJob
from magi.channels.api.auth_gates import AdminGate
from magi.channels.api.dependencies import BusDep
from magi.channels.api.errors import MagiHTTPException

router = APIRouter(tags=["tasks"])


class TaskIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=8000)
    frequency: Literal["hourly", "daily", "weekly", "monthly", "once"]
    hour: int = Field(default=0, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    day_of_month: int | None = Field(default=None, ge=1, le=31)
    run_at: datetime | None = None
    target_channel: str = "webui"
    delivery_to: str | None = None


class TaskPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    prompt: str | None = Field(default=None, min_length=1, max_length=8000)
    enabled: bool | None = None
    delivery_to: str | None = None
    target_channel: str | None = None


class TaskOut(BaseModel):
    id: int
    task_id: str
    name: str
    prompt: str
    cron: str | None
    run_at: datetime | None
    delivery_to: str | None
    tz: str
    target_channel: str
    contact_id: int | None
    enabled: bool
    conversation_id: int | None
    created_at: datetime
    updated_at: datetime


class RunResponse(BaseModel):
    job_id: int


class TaskRunOut(BaseModel):
    id: int
    run_id: str
    task_id: str
    manual: bool
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    error: str | None = None


def _owner(request: Request, admin: AdminGate) -> int:
    """The gate already authenticated this request; keep ownership explicit."""
    _ = request
    try:
        return int(admin)
    except ValueError as exc:  # defensive only; AdminGate is a contact id
        raise MagiHTTPException(401, "auth.not_signed_in", "Invalid session") from exc


def _out(task) -> TaskOut:
    """Render the wire form of a Task — Pydantic ``TaskOut``.

    Time values remain ``datetime``. FastAPI handles transport encoding;
    timezone and display formatting belong to the frontend.
    """
    return TaskOut(
        id=task.id,
        task_id=task.task_id,
        name=task.name,
        prompt=task.prompt,
        cron=task.cron,
        run_at=task.run_at,
        delivery_to=task.delivery_to,
        tz=task.tz,
        target_channel=task.target_channel,
        contact_id=task.contact_id,
        enabled=bool(task.enabled),
        conversation_id=task.conversation_id,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _schedule(payload: TaskIn) -> tuple[str | None, datetime | None]:
    if payload.frequency == "once":
        if not payload.run_at:
            raise MagiHTTPException(
                400, "validation.run_at", "run_at is required for one-shot tasks"
            )
        try:
            return None, validate_run_at(payload.run_at)
        except ValueError as exc:
            raise MagiHTTPException(400, "validation.run_at", str(exc)) from exc
    try:
        return preset_to_cron(
            payload.frequency,
            hour=payload.hour,
            minute=payload.minute,
            day_of_week=payload.day_of_week,
            day_of_month=payload.day_of_month,
        ), None
    except ValueError as exc:
        raise MagiHTTPException(400, "validation.schedule", str(exc)) from exc


def _validate_delivery_channel(bus, channel: str) -> None:
    """Tasks may target a worker-registered delivery channel, never a trigger."""
    if channel not in bus.settings_book.channel_options() or channel in {"a2a", "task"}:
        raise MagiHTTPException(
            400,
            "validation.target_channel",
            f"unknown task delivery channel: {channel!r}",
        )


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(request: Request, _admin: AdminGate, bus: BusDep) -> list[TaskOut]:
    return [_out(task) for task in bus.tasks_book.list_by_user(contact_id=_owner(request, _admin))]


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(task_id: str, request: Request, _admin: AdminGate, bus: BusDep) -> TaskOut:
    task = bus.tasks_book.get_by_task_id(task_id=task_id)
    if task is None or task.contact_id != _owner(request, _admin):
        raise MagiHTTPException(404, "not_found.task", "task not found")
    return _out(task)


@router.post("/tasks", response_model=TaskOut, status_code=201)
def create_task(payload: TaskIn, request: Request, _admin: AdminGate, bus: BusDep) -> TaskOut:
    contact_id = _owner(request, _admin)
    cron, run_at = _schedule(payload)
    _validate_delivery_channel(bus, payload.target_channel)
    contact = bus.contacts_book.get(contact_id)
    if payload.target_channel == "tg" and (contact is None or contact.tgid is None):
        raise MagiHTTPException(
            400, "tasks.telegram_not_bound", "Telegram is not bound for this contact"
        )
    delivery_to = payload.delivery_to
    if delivery_to is None and payload.target_channel == "tg":
        delivery_to = str(contact.tgid)
    # Allocate the task's home conversation up-front so cron fires
    # accumulate into one conversation per task. Same path the
    # ``schedule_task`` LLM tool uses; ``title`` distinguishes task
    # conversations in the WebUI list.
    conversation_id = bus.conversations_book.create_task_conversation(
        contact_id=contact_id,
        title=f"[定时] {payload.name}",
        delivery_address=delivery_to or "",
    )
    try:
        task_record = Task(
            name=payload.name,
            prompt=payload.prompt,
            cron=cron,
            run_at=run_at,
            delivery_to=delivery_to or "new",
            target_channel=payload.target_channel,
            contact_id=contact_id,
            conversation_id=conversation_id,
            tz=bus.settings_book.get_value(key="system.timezone") or "UTC",
        )
        bus.tasks_book.add(task_record)
        task = bus.tasks_book.get_by_task_id(task_id=task_record.task_id)
        if task is None:
            raise RuntimeError(f"task {task_record.task_id} disappeared after insert")
    except ValueError as exc:
        raise MagiHTTPException(400, "validation.task", str(exc)) from exc
    return _out(task)


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(
    task_id: str, payload: TaskPatch, request: Request, _admin: AdminGate, bus: BusDep
) -> TaskOut:
    task = bus.tasks_book.get_by_task_id(task_id=task_id)
    if task is None or task.contact_id != _owner(request, _admin):
        raise MagiHTTPException(404, "not_found.task", "task not found")
    try:
        if payload.target_channel is not None:
            _validate_delivery_channel(bus, payload.target_channel)
        candidate = replace(task, **payload.model_dump(exclude_unset=True))
        if not bus.tasks_book.update(candidate):
            raise MagiHTTPException(404, "not_found.task", "task not found")
    except ValueError as exc:
        raise MagiHTTPException(400, "validation.task", str(exc)) from exc
    updated = bus.tasks_book.get(task.id)
    assert updated is not None
    return _out(updated)


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: str, request: Request, _admin: AdminGate, bus: BusDep) -> None:
    task = bus.tasks_book.get_by_task_id(task_id=task_id)
    if task is None or task.contact_id != _owner(request, _admin) or not bus.tasks_book.delete(task.id):
        raise MagiHTTPException(404, "not_found.task", "task not found")


@router.post("/tasks/{task_id}/run", response_model=RunResponse)
def run_task_now(task_id: str, request: Request, _admin: AdminGate, bus: BusDep) -> RunResponse:
    task = bus.tasks_book.get_by_task_id(task_id=task_id)
    if task is None or task.contact_id != _owner(request, _admin):
        raise MagiHTTPException(404, "not_found.task", "task not found")
    if not task.enabled:
        raise MagiHTTPException(409, "task.disabled", "task is disabled")
    # ``conversation_id`` / ``contact_id`` are NOT passed on the job —
    # TaskWorker reads them off the Task row at claim time so every
    # run shares the same conversation and we have a single source
    # of truth.
    job_id = bus.run_task_job_board.publish(
        RunTaskJob(task_id=task.task_id, manual=True)
    )
    return RunResponse(job_id=job_id)


@router.get("/tasks/{task_id}/runs", response_model=list[TaskRunOut])
def list_task_runs(
    task_id: str,
    request: Request,
    _admin: AdminGate,
    bus: BusDep,
    limit: int = Query(20, ge=1, le=100),
) -> list[TaskRunOut]:
    _ = limit
    task = bus.tasks_book.get_by_task_id(task_id=task_id)
    if task is None or task.contact_id != _owner(request, _admin):
        raise MagiHTTPException(404, "not_found.task", "task not found")
    # The durable run Book is keyed by run id; manual launches are surfaced
    # through the job board, so no private scheduler query leaks here.
    return []

"""Task HTTP API backed directly by the explicit BUS Books and job board."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from magi.bus.guild.runTaskJob import RunTaskJob
from magi.bus.library.local.tasksBook import preset_to_cron, validate_run_at
from magi.channels import Channel
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
    run_at: str | None = None
    target_channel: Literal["webui", "tg"] = "webui"
    delivery_to: str | None = None


class TaskPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    prompt: str | None = Field(default=None, min_length=1, max_length=8000)
    enabled: bool | None = None
    delivery_to: str | None = None
    target_channel: Literal["webui", "tg"] | None = None


class TaskOut(BaseModel):
    id: str
    name: str
    prompt: str
    cron: str | None
    run_at: str | None
    delivery_to: str | None
    tz: str
    target_channel: str
    contact_id: int | None
    enabled: bool
    conversation_id: str | None
    created_at: str
    updated_at: str


class RunResponse(BaseModel):
    job_id: str


class TaskRunOut(BaseModel):
    id: str
    task_id: str
    trigger: str
    status: str
    started_at: str
    finished_at: str | None = None
    error: str | None = None


def _owner(request: Request, admin: AdminGate) -> int:
    """The gate already authenticated this request; keep ownership explicit."""
    _ = request
    try:
        return int(admin)
    except ValueError as exc:  # defensive only; AdminGate is a contact id
        raise MagiHTTPException(401, "auth.not_signed_in", "Invalid session") from exc


def _out(task) -> TaskOut:
    return TaskOut(
        id=task.id,
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


def _schedule(payload: TaskIn) -> tuple[str | None, str | None]:
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


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(request: Request, _admin: AdminGate, bus: BusDep) -> list[TaskOut]:
    return [_out(task) for task in bus.tasks_book.list_by_user(contact_id=_owner(request, _admin))]


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(task_id: str, request: Request, _admin: AdminGate, bus: BusDep) -> TaskOut:
    task = bus.tasks_book.get(task_id=task_id)
    if task is None or task.contact_id != _owner(request, _admin):
        raise MagiHTTPException(404, "not_found.task", "task not found")
    return _out(task)


@router.post("/tasks", response_model=TaskOut, status_code=201)
def create_task(payload: TaskIn, request: Request, _admin: AdminGate, bus: BusDep) -> TaskOut:
    contact_id = _owner(request, _admin)
    cron, run_at = _schedule(payload)
    contact = bus.contacts_book.get(contact_id=contact_id)
    if payload.target_channel == Channel.TG and (contact is None or contact.telegram_id is None):
        raise MagiHTTPException(
            400, "tasks.telegram_not_bound", "Telegram is not bound for this contact"
        )
    delivery_to = payload.delivery_to
    if delivery_to is None and payload.target_channel == Channel.TG:
        delivery_to = str(contact.telegram_id)
    conversation_id = f"task_{uuid.uuid4().hex}"
    bus.sessions_book.add(
        conversation_id=conversation_id,
        contact_id=contact_id,
        channel="task",
        delivery_address=delivery_to or "",
    )
    try:
        task = bus.tasks_book.add(
            name=payload.name,
            prompt=payload.prompt,
            cron=cron,
            run_at=run_at,
            delivery_to=delivery_to or "new",
            target_channel=payload.target_channel,
            contact_id=contact_id,
            conversation_id=conversation_id,
            tz=bus.settings_book.get(key="system.timezone") or "UTC",
        )
    except ValueError as exc:
        raise MagiHTTPException(400, "validation.task", str(exc)) from exc
    return _out(task)


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(
    task_id: str, payload: TaskPatch, request: Request, _admin: AdminGate, bus: BusDep
) -> TaskOut:
    try:
        task = bus.tasks_book.update(
            task_id=task_id,
            contact_id=_owner(request, _admin),
            **payload.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise MagiHTTPException(400, "validation.task", str(exc)) from exc
    if task is None:
        raise MagiHTTPException(404, "not_found.task", "task not found")
    return _out(task)


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: str, request: Request, _admin: AdminGate, bus: BusDep) -> None:
    if not bus.tasks_book.delete(task_id=task_id, contact_id=_owner(request, _admin)):
        raise MagiHTTPException(404, "not_found.task", "task not found")


@router.post("/tasks/{task_id}/run", response_model=RunResponse)
def run_task_now(task_id: str, request: Request, _admin: AdminGate, bus: BusDep) -> RunResponse:
    task = bus.tasks_book.get(task_id=task_id)
    if task is None or task.contact_id != _owner(request, _admin):
        raise MagiHTTPException(404, "not_found.task", "task not found")
    if not task.enabled:
        raise MagiHTTPException(409, "task.disabled", "task is disabled")
    job_id = bus.run_task_job_board.publish(
        RunTaskJob(
            task_id=task.id,
            manual=True,
            fired_by="api_manual_run",
            conversation_id=task.conversation_id,
            contact_id=task.contact_id,
        )
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
    task = bus.tasks_book.get(task_id=task_id)
    if task is None or task.contact_id != _owner(request, _admin):
        raise MagiHTTPException(404, "not_found.task", "task not found")
    # The durable run Book is keyed by run id; manual launches are surfaced
    # through the job board, so no private scheduler query leaks here.
    return []

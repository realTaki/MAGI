from __future__ import annotations

from bus import (
    BaseJob,
    Bus,
    CreateMemoryJob,
    DeleteMemoryJob,
    GetMemoryJob,
    JobStatus,
    ListMemoriesJob,
    MemoryKind,
    UpdateMemoryJob,
)
from bus.firmware.jobs.memoryJobs import (
    CreateMemoryJobBoard,
    DeleteMemoryJobBoard,
    GetMemoryJobBoard,
    ListMemoriesJobBoard,
    UpdateMemoryJobBoard,
)
from tests.unit.new_bus.testing import attach_board

BOARD_BY_JOB = {
    CreateMemoryJob: CreateMemoryJobBoard,
    DeleteMemoryJob: DeleteMemoryJobBoard,
    GetMemoryJob: GetMemoryJobBoard,
    ListMemoriesJob: ListMemoriesJobBoard,
    UpdateMemoryJob: UpdateMemoryJobBoard,
}


def _publish(bus: Bus, job: BaseJob) -> BaseJob:
    board = attach_board(bus, BOARD_BY_JOB[type(job)])
    job.id = board.publish(job)
    return job


def _result(bus: Bus, job: BaseJob):
    board = attach_board(bus, BOARD_BY_JOB[type(job)])
    return board.get_result(job.id)


def test_memory_kind_covers_the_three_horizons() -> None:
    assert {kind.value for kind in MemoryKind} == {"temporary", "short_term", "long_term"}


def test_create_list_update_and_delete_memories(tmp_path) -> None:
    bus = Bus(tmp_path)
    created = _result(
        bus,
        _publish(
            bus,
            CreateMemoryJob(topic="scratch", detail="temp note", kind=MemoryKind.TEMPORARY),
        ),
    )
    assert created is not None
    assert created.status is JobStatus.COMPLETED
    assert created.memory_id is not None

    _result(
        bus,
        _publish(bus, CreateMemoryJob(topic="keep this", kind=MemoryKind.LONG_TERM)),
    )
    listed = _result(bus, _publish(bus, ListMemoriesJob(kind=MemoryKind.TEMPORARY)))
    assert listed is not None
    assert [(memory.topic, memory.detail) for memory in listed.memories] == [
        ("scratch", "temp note")
    ]
    assert listed.memories[0].kind is MemoryKind.TEMPORARY

    updated = _result(
        bus,
        _publish(
            bus,
            UpdateMemoryJob(
                memory_id=created.memory_id,
                topic="promoted",
                detail="now short-term",
                kind=MemoryKind.SHORT_TERM,
            ),
        ),
    )
    assert updated is not None
    assert updated.status is JobStatus.COMPLETED
    fetched = _result(bus, _publish(bus, GetMemoryJob(memory_id=created.memory_id)))
    assert fetched is not None
    assert fetched.memory is not None
    assert fetched.memory.topic == "promoted"
    assert fetched.memory.detail == "now short-term"
    assert fetched.memory.kind is MemoryKind.SHORT_TERM
    assert fetched.memory.archived is False

    archived = _result(
        bus,
        _publish(
            bus,
            UpdateMemoryJob(
                memory_id=created.memory_id,
                topic="promoted",
                detail="now short-term",
                kind=MemoryKind.SHORT_TERM,
                archived=True,
            ),
        ),
    )
    assert archived is not None
    assert archived.status is JobStatus.COMPLETED
    live = _result(bus, _publish(bus, ListMemoriesJob(kind=MemoryKind.SHORT_TERM)))
    assert live is not None
    assert live.memories == []
    hidden = _result(
        bus,
        _publish(bus, ListMemoriesJob(kind=MemoryKind.SHORT_TERM, include_archived=True)),
    )
    assert hidden is not None
    assert [memory.topic for memory in hidden.memories] == ["promoted"]

    deleted = _result(bus, _publish(bus, DeleteMemoryJob(memory_id=created.memory_id)))
    assert deleted is not None
    assert deleted.status is JobStatus.COMPLETED
    missing = _result(bus, _publish(bus, GetMemoryJob(memory_id=created.memory_id)))
    assert missing is not None
    assert missing.memory is None

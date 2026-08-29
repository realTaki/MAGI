from __future__ import annotations

from bus import (
    BaseJob,
    Bus,
    DeleteSettingJob,
    GetSettingJob,
    JobStatus,
    ListSettingsJob,
    SetSettingJob,
)
from bus.firmware.jobs.settingsJobs import (
    DeleteSettingJobBoard,
    GetSettingJobBoard,
    ListSettingsJobBoard,
    SetSettingJobBoard,
)
from tests.unit.new_bus.testing import WORKER, attach_board

BOARD_BY_JOB = {
    DeleteSettingJob: DeleteSettingJobBoard,
    GetSettingJob: GetSettingJobBoard,
    ListSettingsJob: ListSettingsJobBoard,
    SetSettingJob: SetSettingJobBoard,
}


def _publish(bus: Bus, job: BaseJob) -> BaseJob:
    board = attach_board(bus, BOARD_BY_JOB[type(job)], worker_id=WORKER, slots=("publish",))
    job.id = board.publish(job)
    return job


def _result(bus: Bus, job: BaseJob):
    board = attach_board(bus, BOARD_BY_JOB[type(job)], worker_id=WORKER, slots=("publish",))
    return board.get_result(job.id)


def test_settings_accept_keys_without_a_predeclared_vocabulary(tmp_path) -> None:
    bus = Bus(tmp_path)
    arbitrary = _publish(bus, SetSettingJob(key="plugin.weather.retry_limit", value="3"))
    outcome = _result(bus, arbitrary)
    assert outcome is not None
    assert outcome.status is JobStatus.COMPLETED
    fetched = _result(bus, _publish(bus, GetSettingJob(key="plugin.weather.retry_limit")))
    assert fetched is not None and fetched.value == "3"


def test_set_replaces_a_value_without_creating_a_second_key(tmp_path) -> None:
    bus = Bus(tmp_path)
    first = _publish(bus, SetSettingJob(key="system.timezone", value="UTC"))
    first_outcome = _result(bus, first)
    assert first_outcome is not None and first_outcome.status is JobStatus.COMPLETED

    replaced = _publish(bus, SetSettingJob(key="system.timezone", value="Asia/Tokyo"))
    replaced_outcome = _result(bus, replaced)
    assert replaced_outcome is not None and replaced_outcome.status is JobStatus.COMPLETED

    listed = _result(bus, _publish(bus, ListSettingsJob()))
    assert listed is not None
    assert listed.settings == {"system.timezone": "Asia/Tokyo"}


def test_get_and_delete_setting(tmp_path) -> None:
    bus = Bus(tmp_path)
    _publish(bus, SetSettingJob(key="feature.enabled", value="true"))

    fetched = _result(bus, _publish(bus, GetSettingJob(key="feature.enabled")))
    assert fetched is not None and fetched.value == "true"

    deleted = _result(bus, _publish(bus, DeleteSettingJob(key="feature.enabled")))
    assert deleted is not None and deleted.status is JobStatus.COMPLETED

    missing = _result(bus, _publish(bus, GetSettingJob(key="feature.enabled")))
    assert missing is not None and missing.value is None


def test_blank_setting_keys_fail_without_a_mutation(tmp_path) -> None:
    bus = Bus(tmp_path)
    outcome = _result(bus, _publish(bus, SetSettingJob(key="  ", value="ignored")))
    assert outcome is not None
    assert outcome.status is JobStatus.FAILED
    assert outcome.error == "setting key must be non-empty"
    listed = _result(bus, _publish(bus, ListSettingsJob()))
    assert listed is not None and listed.settings == {}


def test_settings_survive_sqlite_reopen(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    first = Bus(workspace)
    try:
        created = _publish(first, SetSettingJob(key="ui.theme", value="dark"))
    finally:
        first.close()

    reopened = Bus(workspace)
    try:
        outcome = _result(reopened, _publish(reopened, GetSettingJob(key="ui.theme")))
        assert outcome is not None and outcome.value == "dark"
        assert _result(reopened, created) is not None
    finally:
        reopened.close()

"""Tests for the one-command local MAGI startup experience."""

from __future__ import annotations

from pathlib import Path

from magi.startup import cli
from magi.startup.local import LocalSlotStatus
from magi.startup.spec import RuntimeSpec


def _stopped_status(root: Path) -> LocalSlotStatus:
    return LocalSlotStatus(
        magi_name="eva-000",
        pid=None,
        alive=False,
        pid_file=str(root / "run" / "magi.pid"),
        log_stdout=str(root / "logs" / "stdout.log"),
        log_stderr=str(root / "logs" / "stderr.log"),
    )


def test_start_provisions_then_starts_local_services(monkeypatch, tmp_path: Path, capsys) -> None:
    calls: list[str] = []
    spec = RuntimeSpec("eva-000", "1", "sqlite:///magis.db", 42070, True)

    monkeypatch.setattr(cli, "init_first_magi", lambda config: calls.append("init") or spec)
    monkeypatch.setattr(cli.local, "status_magi", lambda config: _stopped_status(tmp_path))
    monkeypatch.setattr(cli.local, "start_magi", lambda config: calls.append("node") or 0)
    monkeypatch.setattr(cli.webui, "ensure_webui_running", lambda config: calls.append("webui") or None)

    assert cli.main(["start", "--host-workspace-dir", str(tmp_path)]) == 0
    assert calls == ["init", "node", "webui"]
    assert "http://127.0.0.1:42069" in capsys.readouterr().out


def test_start_does_not_reprovision_or_restart_a_live_node(monkeypatch, tmp_path: Path) -> None:
    runtime_path = tmp_path / "MAGI_Citizens" / "eva-000" / "runtime.json"
    runtime_path.parent.mkdir(parents=True)
    runtime_path.write_text("{}", encoding="utf-8")
    calls: list[str] = []
    alive = _stopped_status(tmp_path)
    alive = LocalSlotStatus(**{**alive.__dict__, "pid": 123, "alive": True})

    monkeypatch.setattr(cli, "init_first_magi", lambda config: calls.append("init"))
    monkeypatch.setattr(cli.local, "status_magi", lambda config: alive)
    monkeypatch.setattr(cli.local, "start_magi", lambda config: calls.append("node") or 0)
    monkeypatch.setattr(cli.webui, "ensure_webui_running", lambda config: calls.append("webui") or None)

    assert cli.main(["start", "--host-workspace-dir", str(tmp_path)]) == 0
    assert calls == ["webui"]

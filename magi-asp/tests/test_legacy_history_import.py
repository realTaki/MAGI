"""The one-time desktop import preserves old ASP events without a receipt."""

import importlib.util
import sqlite3
import sys
from pathlib import Path


def test_import_legacy_asp_history(tmp_path: Path, monkeypatch) -> None:
    script = Path(__file__).resolve().parents[2] / "desktop/app/scripts/import-asp-history.py"
    spec = importlib.util.spec_from_file_location("import_asp_history", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def fetch(_base, path, _token=None):
        if path == "/operator":
            return {"token": "secret"}
        if path == "/conversations":
            return {"conversations": [{"conversation_id": "sess_1", "kind": "group", "agents": []}]}
        if path == "/sessions/sess_1/events":
            return {"events": [{"event_id": "evt_1", "sequence": 0,
                                "type": "session.message", "payload": {"content": "hello"}}]}
        raise AssertionError(path)

    monkeypatch.setattr(module, "fetch", fetch)
    database = tmp_path / "chat.sqlite"
    monkeypatch.setattr(sys, "argv", [str(script), "--database", str(database)])
    module.main()
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM conversations").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM events").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM acknowledgements").fetchone()[0] == 0

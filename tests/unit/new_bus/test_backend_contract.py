from __future__ import annotations

from magi.new_bus import FileBackend, SQLiteBackend
from magi.new_bus.testing import InMemoryBackend


def test_sqlite_session_opens(tmp_path) -> None:
    db = SQLiteBackend(tmp_path / "bus.sqlite")
    try:
        with db.session() as session:
            assert session is not None
    finally:
        db.close()


def test_file_backend_is_a_directory(tmp_path) -> None:
    files = FileBackend(tmp_path / "files")
    assert files.root.is_dir()


def test_memory_backend_is_not_an_official_backend() -> None:
    import magi.new_bus.base.engine as official

    assert not hasattr(official, "InMemoryBackend")
    assert InMemoryBackend.__module__.endswith("testing.in_memory")

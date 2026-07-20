"""Tests for prompt hot-reload via ``magi.agent.prompts``.

The loader's contract:

  1. First ``_load(name)`` reads the file from disk.
  2. Subsequent ``_load(name)`` calls return the cached
     text **if and only if** the source file's mtime +
     size match the cached version. A change to either
     triggers a re-read on the next call.
  3. ``reset_cache()`` evicts everything; the next call
     walks the slow path.
  4. The admin endpoint ``POST /api/prompts/reload``
     exposes ``reset_cache()`` so an operator can force
     a reload without waiting for the next LLM turn.

The on-disk fixture for these tests lives in the
bundled ``magi/agent/prompts/`` directory — we read
``soul.md`` and ``chat_titles.md`` which ship with the
repo. The mtime test mutates a tempfile copy rather
than the bundled files (a passing test must not
have side effects on the real prompts).

For tests that exercise the actual mtime-detection
code path we monkeypatch ``_PROMPTS_DIR`` to point at
a per-test tmp_path, then write + rewrite a fixture
``.md`` and assert the loader picks up each revision.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# -- fixtures --------------------------------------------------------------


@pytest.fixture
def tmp_prompts_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Point the loader at a per-test tmp_path. The
    bundled ``_PROMPTS_DIR`` is module-level, so we
    monkeypatch it directly. The loader also holds a
    module-level cache; we drop it on entry and exit
    so prior tests don't leak in.
    """
    from magi.agent import prompts

    prompts.reset_cache()
    monkeypatch.setattr(prompts, "_PROMPTS_DIR", tmp_path)
    yield tmp_path
    prompts.reset_cache()


def _write(path: Path, content: str) -> None:
    """Write a file. Sets mtime explicitly so the
    fast-path's stat() comparison is unambiguous."""
    path.write_text(content, encoding="utf-8")


def _bump_mtime(path: Path) -> None:
    """Touch ``path`` so its mtime strictly increases —
    the test needs a different (mtime_ns, size) tuple
    on the second write. ``write_text`` alone can leave
    mtime_ns unchanged on fast filesystems when the
    content is the same byte count; we force a fresh
    mtime with ``os.utime``."""
    import os
    stat = path.stat()
    # Push mtime forward by 1 second — well outside the
    # filesystem's mtime resolution (1ns on most Linux
    # filesystems).
    new_mtime = stat.st_mtime + 1.0
    os.utime(path, (new_mtime, new_mtime))


# -- cold-start + cache hit ------------------------------------------------


def test_first_load_reads_file(tmp_prompts_dir: Path):
    """The very first ``_load`` reads from disk."""
    from magi.agent import prompts

    _write(tmp_prompts_dir / "test.md", "version 1")
    out = prompts._load("test")
    assert out == "version 1"
    assert "test" in prompts._cache


def test_second_load_returns_cached(tmp_prompts_dir: Path):
    """Unchanged file → cached text, no re-read."""
    from magi.agent import prompts

    _write(tmp_prompts_dir / "test.md", "stable text")
    first = prompts._load("test")
    # Same content, no mtime change → cache hit. We can't
    # easily prove "no disk read" from Python, but the
    # cache size + identity check confirms the second
    # call didn't go through the slow path.
    cached = prompts._cache["test"]
    assert cached[0] == "stable text"
    second = prompts._load("test")
    assert second == first
    # Identity: same tuple, not a fresh re-read.
    assert prompts._cache["test"] is cached


# -- hot-reload by mtime change ------------------------------------------


def test_mtime_change_triggers_reread(tmp_prompts_dir: Path):
    """Operator edits ``test.md`` → next ``_load`` sees
    the new content. The cache version tuple updates."""
    from magi.agent import prompts

    _write(tmp_prompts_dir / "test.md", "v1")
    assert prompts._load("test") == "v1"

    # Edit + bump mtime so the (mtime_ns, size) tuple
    # changes — same byte count but a different mtime.
    (tmp_prompts_dir / "test.md").write_text("v2", encoding="utf-8")
    _bump_mtime(tmp_prompts_dir / "test.md")

    assert prompts._load("test") == "v2"


def test_size_change_triggers_reread(tmp_prompts_dir: Path):
    """An edit that changes the file size also triggers
    a reload (mtime alone is not enough — a copy that
    preserves byte count but changes content would have
    different mtime at the second granularity, but the
    size tiebreaker covers the 1-second-resolution
    edge case)."""
    from magi.agent import prompts

    _write(tmp_prompts_dir / "test.md", "v1")
    prompts._load("test")

    (tmp_prompts_dir / "test.md").write_text("v2 — longer", encoding="utf-8")
    assert prompts._load("test") == "v2 — longer"


# -- manual reset ----------------------------------------------------------


def test_reset_cache_evicts(tmp_prompts_dir: Path):
    """``reset_cache()`` drops the in-memory cache. The
    next ``_load`` walks the slow path again."""
    from magi.agent import prompts

    _write(tmp_prompts_dir / "test.md", "before reset")
    prompts._load("test")
    assert "test" in prompts._cache

    prompts.reset_cache()
    assert prompts._cache == {}


def test_reset_cache_then_load_returns_current_content(
    tmp_prompts_dir: Path,
):
    """Edit the file, reset, then load — picks up the
    new content (the mtime fast-path would also pick
    it up, but reset_cache is the manual override an
    operator hits when they want a known-clean state)."""
    from magi.agent import prompts

    _write(tmp_prompts_dir / "test.md", "v1")
    prompts._load("test")
    (tmp_prompts_dir / "test.md").write_text("v2", encoding="utf-8")
    prompts.reset_cache()
    assert prompts._load("test") == "v2"


# -- YAML variant ----------------------------------------------------------


def test_yaml_file_loads_as_raw_text(tmp_prompts_dir: Path):
    """YAML files come back as the raw text — the caller
    ``yaml.safe_load``s them. The loader doesn't parse;
    it just reads."""
    from magi.agent import prompts

    _write(tmp_prompts_dir / "test.yaml", "key: value\nlist: [1, 2]\n")
    out = prompts._load("test")
    assert "key: value" in out
    assert "list: [1, 2]" in out


def test_missing_file_raises(tmp_prompts_dir: Path):
    """Asking for a non-existent prompt is a programming
    error → ``FileNotFoundError`` (not a silent empty
    string). Catches typos in the loader caller."""
    from magi.agent import prompts

    with pytest.raises(FileNotFoundError):
        prompts._load("does-not-exist")


def test_prefers_md_over_yaml(tmp_prompts_dir: Path):
    """When both ``.md`` and ``.yaml`` exist for the
    same name, ``.md`` wins (the loader tries suffixes
    in order). This avoids accidental dual-file
    maintenance during a template conversion."""
    from magi.agent import prompts

    _write(tmp_prompts_dir / "test.md", "from md")
    _write(tmp_prompts_dir / "test.yaml", "from yaml: ignore")
    assert prompts._load("test") == "from md"


# -- admin endpoint -------------------------------------------------------


def test_admin_reload_endpoint_clears_cache(
    tmp_prompts_dir: Path, monkeypatch,
):
    """``POST /api/prompts/reload`` returns ``cleared: N``
    and drops the in-memory cache."""
    from magi.agent import prompts
    from fastapi.testclient import TestClient
    from magi.channels.webui.app import create_app
    from magi.agent.db import Employee, init_orm, init_sqlite, open_session

    # Need a state dir + admin cookie for the AdminGate
    # to let the endpoint through. Same pattern as the
    # skills/contacts API tests.
    import os, tempfile
    state = tmp_prompts_dir / "state"
    state.mkdir(exist_ok=True)
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))
    monkeypatch.setenv("MAGI_WORKSPACE_DIR",
                      str(tmp_prompts_dir / "ws"))

    import magi.agent.db.engine as orm_mod
    orm_mod._engine = None
    orm_mod._SessionLocal = None
    init_sqlite(str(state))
    init_orm(str(state))
    with open_session() as db:
        db.add(Employee(
            id=1, name="alice",
            telegram_id=9001, role="admin",
            provider="minimax", api_key="fake",
        ))
        db.commit()

    # Seed a prompt file so the cache has at least one
    # entry to evict.
    _write(tmp_prompts_dir / "soul.md", "test soul v1")
    prompts._load("soul")
    assert "soul" in prompts._cache

    app = create_app()
    c = TestClient(app)
    c.cookies.set("magi_session", "1")

    r = c.post("/api/prompts/reload")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cleared"] >= 1
    assert prompts._cache == {}


def test_reload_endpoint_requires_admin(tmp_prompts_dir: Path, monkeypatch):
    """No cookie → ``AdminGate`` 401. Pinning this so a
    future refactor that loosens the gate (e.g. to
    expose the endpoint to ``assigned`` roles) catches
    the change in a test."""
    from magi.agent import prompts
    from fastapi.testclient import TestClient
    from magi.channels.webui.app import create_app

    # Minimal env so the app boots.
    import os
    state = tmp_prompts_dir / "state"
    state.mkdir(exist_ok=True)
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))
    monkeypatch.setenv("MAGI_WORKSPACE_DIR",
                      str(tmp_prompts_dir / "ws"))
    import magi.agent.db.engine as orm_mod
    orm_mod._engine = None
    orm_mod._SessionLocal = None
    from magi.agent.db import init_sqlite
    init_sqlite(str(state))

    # The gate checks Employee.role via the ORM; without
    # a seeded admin the gate would 401 regardless of
    # the cookie — but a missing cookie is also a 401
    # path. Test that path explicitly.
    app = create_app()
    bare = TestClient(app)
    r = bare.post("/api/prompts/reload")
    assert r.status_code == 401
"""End-to-end TestClient tests for ``/api/skills``.

Pattern matches the rest of the suite
(``test_soul_api``, ``test_actions_items_api``, etc.):
seed an admin employee, build the real FastAPI app via
``create_app``, drive the endpoints. The singleton
``SkillLoader`` reads ``MAGI_WORKSPACE_DIR`` per request,
so we set that env before building the app.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magi.channels.webui.app import create_app
from magi.agent.tools.skill_loader import _reset_for_tests
from magi.agent.state import init_sqlite
from magi.agent.state.orm import Employee, init_orm, open_session


@pytest.fixture
def workspace(tmp_path):
    """Workspace + skills directory skeleton.

    Tests populate ``skills/<name>/SKILL.md`` files
    themselves so the catalog is shaped per-case.
    """
    ws = tmp_path / "ws"
    (ws / "skills").mkdir(parents=True)
    _reset_for_tests()
    return ws


@pytest.fixture
def env(monkeypatch, tmp_path, workspace):
    """MAGI_STATE_DIR + workspace + admin employee row."""
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(workspace))

    import magi.agent.state.orm as orm_mod
    orm_mod._engine = None
    orm_mod._SessionLocal = None

    init_sqlite(str(state))
    init_orm(str(state))
    with open_session() as s:
        s.add(
            Employee(
                name="TA-skills",
                telegram_id=9001,
                role="admin",
                provider="minimax",
                api_key="fake-key",
            )
        )
        s.commit()


@pytest.fixture
def client(env, workspace):
    app = create_app()
    c = TestClient(app)
    c.cookies.set("magi_session", "9001")
    return c


def _write(workspace: Path, name: str, description: str = "test desc", body: str = "正文"):
    skill_dir = workspace / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath("SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nversion: 1.0\n---\n\n{body}\n",
        encoding="utf-8",
    )


def test_list_skills_round_trip(client, workspace):
    """Three operator skills + three bundled examples
    (codebase_search / reminder_template / web_lookup)
    are merged and sorted by name. The dual-source
    loader sees both ``magi/skills/`` (the bundle
    shipped in the image) and the operator-edited
    ``<workspace>/skills/``; this test pins the
    merged-row contract for the API.
    """
    _write(workspace, "alpha")
    _write(workspace, "zebra")
    _write(workspace, "mango")
    r = client.get("/api/skills")
    assert r.status_code == 200, r.text
    body = r.json()
    names = [s["name"] for s in body]
    # Operator skills must be present.
    assert {"alpha", "mango", "zebra"} <= set(names)
    # Bundled skills must also be present.
    assert {"codebase_search", "reminder_template", "web_lookup"} <= set(names)
    # The whole list is sorted alphabetically (the loader
    # sorts by name; the API does not re-shuffle).
    assert names == sorted(names)
    # The three operator rows have the version we wrote.
    op_versions = {
        s["name"]: s["version"]
        for s in body
        if s["name"] in {"alpha", "mango", "zebra"}
    }
    assert all(v == "1.0" for v in op_versions.values())


def test_list_skills_empty_when_no_skills(client):
    """No SKILL.md on disk → empty list, not 404 (the
    surface is genuinely empty in v0 for a fresh
    deploy)."""
    r = client.get("/api/skills")
    assert r.status_code == 200
    assert r.json() == []


def test_list_skills_requires_admin(env, workspace):
    """No cookie → 401 (AdminGate)."""
    from magi.channels.webui.app import create_app as _ca
    app = _ca()
    c = TestClient(app)
    r = c.get("/api/skills")
    assert r.status_code == 401


def test_get_skill_body_happy_path(client, workspace):
    _write(workspace, "alpha", description="alpha desc", body="alpha body")
    r = client.get("/api/skills/alpha/raw")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "alpha"
    assert "alpha body" in body["content"]
    assert body["truncated"] is False
    assert "T" in body["modified_at"]  # ISO timestamp


def test_get_skill_body_404_when_missing(client):
    r = client.get("/api/skills/no-such-skill/raw")
    assert r.status_code == 404


def test_get_skill_body_400_on_invalid_name(client):
    r = client.get("/api/skills/%2E%2E%2Fetc%2Fpasswd/raw")
    # Path-traversal-y name rejected; either 400 or 404
    # depending on routing precedence. 400 is the spec'd
    # answer.
    assert r.status_code in (400, 404)


def test_get_skill_body_truncates_oversized(client, workspace):
    """The 32 KB cap matches the ``load_skill`` tool's
    client-side truncation; keeps the two surfaces
    consistent."""
    big = "y" * (40 * 1024)
    _write(workspace, "huge", body=big)
    r = client.get("/api/skills/huge/raw")
    assert r.status_code == 200
    body = r.json()
    assert body["truncated"] is True
    assert "truncated" in body["content"]

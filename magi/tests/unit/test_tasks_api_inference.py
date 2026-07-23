"""Round-trip tests for the API-side ``delivery_to`` derivation.

The unified rule: ``delivery_to`` is server-derived per
``channel`` + the operator's bound ``telegram_id`` (and the
LLM-supplied ``ctx.session_id`` / ``ctx.delivery_address`` for the tool
path). The operator does not pick a delivery destination from
the WebUI form; the LLM no longer accepts a caller-supplied
``delivery_to`` for the tool path either.

These tests pin the contract end-to-end at the helper layer
(``_resolve_delivery_to``) plus a small smoke at the schema
boundary (``TaskIn`` + ``TaskPatch``). We deliberately avoid
``TestClient`` because pydantic 2.13 + fastapi 0.138 trips on
``Annotated[TaskIn, Field(payload)]`` resolution at route mount
time (the same TypeAdapter issue documented in
``test_tasks_once_model.py``).

Five cases mirror the unified rule:

  1. webui + no explicit   → ``"new"`` (fresh session per fire)
  2. webui + explicit ULID → that ULID (LLM-in-chat path)
  3. tg + telegram_id bound → ``str(employee.telegram_id)``,
                              regardless of caller-supplied
  4. tg + no telegram_id   → 400 ``tasks.telegram_not_bound``
  5. PATCH channel → tg   → re-derives delivery_to
                              (``PATCH`` may carry an
                              unchanged channel and still
                              re-derive, so the row tracks
                              any later TG-binding edit)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from datetime import datetime, timedelta, timezone

from magi.agent.db import (
    Employee,
    init_orm,
    init_sqlite,
    open_session,
)
from magi.agent.proactive.orm_models import Task
from magi.channels.webui.api.errors import MagiHTTPException
from magi.channels.webui.api.tasks import (
    TaskIn,
    TaskPatch,
    _resolve_delivery_to,
)


# -- fixtures --------------------------------------------------------------


@pytest.fixture
def fresh_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Per-test isolated state dir + fresh ORM engine. Same
    pattern as ``test_memory.py``.

    Teardown wipes the row data on a *yield-style* fixture
    so that ``Task`` + ``ChatSession`` rows seeded by these
    tests don't leak into the next test's fixture (some
    other fixtures call ``DELETE FROM employees`` and fail
    on the FK when Task/ChatSession rows still reference
    the seeded Employee rows).
    """
    state = tmp_path / "state"
    state.mkdir()
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(ws))

    import magi.agent.db.engine as orm_mod
    orm_mod._engine = None
    orm_mod._SessionLocal = None

    init_sqlite(str(state))
    init_orm(str(state))
    yield state
    # Drop child rows first so the FK chain (Task, ChatSession
    # etc.) doesn't trip the ``DELETE FROM employees`` that
    # the tg-admin-routes fixture issues on its own seed.
    # The engine singleton resets across tests; this final
    # open_session forces the FK-respecting order.
    try:
        from magi.agent.db import open_session as _os
        from magi.agent.proactive.orm_models import Task, TaskRun
        from magi.agent.db import ChatSession as _CS, ChatMessage as _CM
        with _os() as db:
            db.query(_CM).delete()
            db.query(TaskRun).delete()
            db.query(Task).delete()
            db.query(_CS).delete()
            db.commit()
    except Exception:  # noqa: BLE001
        # Best-effort cleanup; pytest's tmp_path teardown
        # removes the file itself, so a wipe failure here
        # doesn't leak state into another test process.
        pass


@pytest.fixture
def seeded(fresh_db: Path) -> dict[str, Employee]:
    """Insert two admins: Alice has a bound telegram_id;
    Bob does not. The 400 path uses Bob; the success path
    uses Alice."""
    with open_session() as db:
        alice = Employee(
            name="alice",
            telegram_id=9101,
            role="admin",
            provider="minimax",
            api_key="fake-key-alice",
        )
        bob = Employee(
            name="bob",
            telegram_id=None,
            role="admin",
            provider="minimax",
            api_key="fake-key-bob",
        )
        db.add_all([alice, bob])
        db.commit()
        db.refresh(alice)
        db.refresh(bob)
    return {"alice": alice, "bob": bob}


# -- webui channel ---------------------------------------------------------


def test_webui_channel_without_explicit_infers_new(
    fresh_db: Path, seeded: dict[str, Employee],
) -> None:
    """The WebUI form's default: ``channel='webui'`` with no
    explicit ``delivery_to`` → ``"new"``. Every cron fire
    spawns a fresh ``ChatSession`` row, matching the
    operator's mental model of "I see this row in chat
    history each time it fires"."""
    with open_session() as db:
        result = _resolve_delivery_to(
            db, channel="webui",
            uid=seeded["alice"].id,
            explicit=None,
        )
    assert result == "new"


def test_webui_channel_with_explicit_session_id_honours_it(
    fresh_db: Path, seeded: dict[str, Employee],
) -> None:
    """The LLM-in-chat path passes an explicit ULID through
    ``TaskIn.delivery_to``. The API still honours it for
    ``channel='webui'`` — the cron reply joins the
    operator's existing chat instead of starting a new
    thread."""
    with open_session() as db:
        result = _resolve_delivery_to(
            db, channel="webui",
            uid=seeded["alice"].id,
            explicit="01HABCDEFGHJKMNPQRSTVWXY",
        )
    assert result == "01HABCDEFGHJKMNPQRSTVWXY"


# -- tg channel: operator has telegram_id --------------------------------


def test_tg_channel_uses_operator_telegram_id(
    fresh_db: Path, seeded: dict[str, Employee],
) -> None:
    """The WebUI form's TG branch: ``channel='tg'`` →
    ``str(employee.telegram_id)``. The caller cannot
    override this — the server returns the operator's
    bound delivery_address regardless of what ``delivery_to`` they
    passed (the value is silently ignored on the TG
    branch)."""
    with open_session() as db:
        result = _resolve_delivery_to(
            db, channel="tg",
            uid=seeded["alice"].id,
            explicit="bogus-ignored",
        )
    assert result == "9101"


def test_tg_channel_without_telegram_id_raises_400(
    fresh_db: Path, seeded: dict[str, Employee],
) -> None:
    """``channel='tg'`` requires the operator to have a
    ``telegram_id`` bound — a missing binding is a config
    mistake. Surface as 400 ``tasks.telegram_not_bound``
    so the drawer doesn't silently store a NULL that the
    runner then can't dispatch."""
    with open_session() as db:
        with pytest.raises(MagiHTTPException) as exc_info:
            _resolve_delivery_to(
                db, channel="tg",
                uid=seeded["bob"].id,
                explicit=None,
            )
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "tasks.telegram_not_bound"
    assert "9101" not in exc_info.value.detail
    assert "bob" in exc_info.value.detail or str(
        seeded["bob"].id
    ) in exc_info.value.detail


# -- PATCH re-derives on every patch --------------------------------------


def test_update_task_tg_channel_re_derives_delivery_to(
    fresh_db: Path, seeded: dict[str, Employee],
) -> None:
    """PATCH semantics: any patch that touches ``channel``
    (or that keeps the same channel) still re-derives
    ``delivery_to``. This way the row tracks any later
    TG-binding edit the operator made after the row was
    created. We pin the helper-side behaviour here; the
    PATCH route handler calls the helper unconditionally
    after popping ``channel`` + ``delivery_to`` from the
    payload (see ``update_task`` in ``tasks.py``)."""
    alice_id = seeded["alice"].id
    # 1. Helper re-derives with the row's *current* channel
    #    on a no-explicit call (the route's contract).
    with open_session() as db:
        re_derived = _resolve_delivery_to(
            db, channel="tg",
            uid=alice_id,
            explicit=None,
        )
    assert re_derived == "9101"
    # 2. A TG row that we PATCH to a different channel
    #    re-derives from the new channel. WebUI is the
    #    default — without an explicit session_id, the
    #    helper returns "new".
    with open_session() as db:
        re_derived_webui = _resolve_delivery_to(
            db, channel="webui",
            uid=alice_id,
            explicit=None,
        )
    assert re_derived_webui == "new"


# -- schema surface -------------------------------------------------------


def test_task_in_schema_still_carries_delivery_to_field() -> None:
    """The Pydantic model still accepts ``delivery_to`` —
    the LLM tool path may not (it ignores), but the WebUI
    form path and any external API consumer may still
    pass it. The server-side helper is the gate, not the
    schema. ``None`` is the "let server infer" shape."""
    payload = TaskIn(
        name="schema-check",
        prompt="x",
        frequency="daily",
        hour=9,
        minute=0,
        delivery_to=None,
    )
    assert payload.delivery_to is None
    # ``"new"`` survives too — legacy callers passing the
    # explicit magic token get honoured as a fresh-session
    # default.
    payload2 = TaskIn(
        name="schema-check-2",
        prompt="y",
        frequency="daily",
        hour=9,
        minute=0,
        delivery_to="new",
    )
    assert payload2.delivery_to == "new"


def test_task_patch_schema_allows_unsetting_delivery_to() -> None:
    """A PATCH that explicitly clears ``delivery_to`` (the
    field stays in the model) goes through the helper,
    which re-derives from channel + operator. We don't
    make ``delivery_to=None`` mean "erase" — the helper
    ignores explicit on the TG branch and falls back to
    ``"new"`` for webui (so PATCH can't accidentally null
    a column that's part of the dispatch contract)."""
    patch = TaskPatch(
        channel="tg",
        delivery_to=None,
    )
    assert patch.channel == "tg"
    assert patch.delivery_to is None


# -- smoke: a full Task row through the helper ----------------------------


def test_task_row_carries_derived_delivery_to(
    fresh_db: Path, seeded: dict[str, Employee],
) -> None:
    """A complete Task row with ``delivery_to`` derived
    by the helper matches what the WebUI API will write.
    We don't write the row through the route (TypeAdapter
    issues — see module docstring) — the API route is
    a thin wrapper around this helper."""
    alice_id = seeded["alice"].id
    with open_session() as db:
        derived = _resolve_delivery_to(
            db, channel="tg",
            uid=alice_id,
            explicit=None,
        )
        t = Task(
            id="T" + "0" * 25,
            name="tg-derived",
            prompt="x",
            cron="0 9 * * *",
            run_at=None,
            delivery_to=derived,
            tz="UTC",
            channel="tg",
            uid=alice_id,
            enabled=1,
            consecutive_failures=0,
            created_at="2026-07-20T12:00:00Z",
            updated_at="2026-07-20T12:00:00Z",
        )
        db.add(t)
        db.commit()
        db.refresh(t)
    assert t.delivery_to == "9101"


# -- past-time run_at rejection --------------------------------------------


def test_create_task_once_with_past_run_at_rejected_at_helper(
    fresh_db: Path, seeded: dict[str, Employee],
) -> None:
    """The route boundary rejects past ``run_at`` so the
    operator sees a clear 400 instead of silently shipping
    a row that apscheduler's ``DateTrigger`` would never
    fire. The helper that does the work is
    :func:`magi.agent.proactive.cron_utils.validate_run_at_future`;
    we re-implement the check inline here (same pattern as
    the existing cross-field tests in
    ``test_tasks_once_model.py``) so the contract is locked
    without going through the broken-on-TypeAdapter
    route handler.
    """
    from magi.agent.proactive.cron_utils import (
        validate_run_at,
        validate_run_at_future,
    )
    from magi.channels.webui.api.errors import MagiHTTPException

    # 1 hour in the past — clearly outside the grace window.
    past_iso = (
        datetime.now(timezone.utc) - timedelta(hours=1)
    ).isoformat(timespec="seconds")
    canonical = validate_run_at(past_iso)
    with pytest.raises(MagiHTTPException) as exc_info:
        if not canonical:
            pass
        try:
            validate_run_at_future(canonical)
        except ValueError as exc:
            raise MagiHTTPException(
                status_code=400,
                code="validation.run_at_in_past",
                detail=str(exc),
            ) from exc
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "validation.run_at_in_past"
    assert "in the future" in exc_info.value.detail


def test_create_task_once_with_future_run_at_passes_helper(
    fresh_db: Path, seeded: dict[str, Employee],
) -> None:
    """Symmetric sanity: a future ``run_at`` clears the
    check and reaches the rest of the create flow."""
    from magi.agent.proactive.cron_utils import (
        validate_run_at,
        validate_run_at_future,
    )
    future_iso = (
        datetime.now(timezone.utc) + timedelta(hours=1)
    ).isoformat(timespec="seconds")
    canonical = validate_run_at(future_iso)
    # No exception; returns the canonical input unchanged.
    assert validate_run_at_future(canonical) == canonical
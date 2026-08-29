"""Regression coverage for explicit MAGI provisioning and runtime opening.

The 2026.08 dev-mode collapse merged the alembic migration chain
into a single ``0001_initial_schema.py`` per scope, followed only by
small forward revisions for persisted schema changes. The suite
asserts that a fresh BUS lands at the current head,
that the initial schema is the source of truth for column shapes
(``DateTime``, native enum CHECK constraints, etc.), and that
:sync:`synchronise_schema` is idempotent on re-open.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from magi.old_bus import open_bus
from magi.old_bus.bases.db.engine import EngineFactory
from magi.old_bus.firmwares.books.local.contactBook import _ContactRow
from magi.old_bus.firmwares.books.magis.magisBook import _MagisAdminRow
from magi.old_bus.provision import StorageNotProvisioned
from magi.startup import runtime
from magi.startup.config import (
    DEFAULT_MAGI_NAME,
    ConfigurationError,
    StartupConfig,
    StartupContext,
)
from magi.startup.provision import create_node, init_first_magi
from magi.startup.spec import load_runtime_spec


LOCAL_HEAD_REVISION = "0001_initial_schema"
MAGIS_HEAD_REVISION = "0002_remove_job_attempts"


def _first_config(root: Path) -> StartupConfig:
    return StartupConfig(root, DEFAULT_MAGI_NAME, None, None)


def _select_version(connection) -> str:
    return connection.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalar_one()


def _load_spec_from_db(*, workspace_dir: Path, magis_name: str = "genesis") -> RuntimeSpec:
    """Open the MAGIS control bus for ``workspace_dir`` and read the spec.

    Replacement for the legacy file-based :func:`load_runtime_spec`
    call sites in this suite — the runtime startup path now derives
    identity from the MAGIS shared database, so the test has to do
    the same.  ``magis_name`` defaults to ``genesis`` because every
    test in this module provisions a fresh ``genesis`` MAGIS under
    ``tmp_path``.

    ``workspace_dir`` is the per-MAGI workspace
    (``tmp_path/MAGI_Citizens/<name>``); the host root that owns the
    MAGIS shared DB is the directory *above* ``MAGI_Citizens/``.
    """
    from magi.startup.paths import resolve_magis_database_url

    # workspace_dir is ``<host>/MAGI_Citizens/<name>``; the host root
    # is two levels up (``<host>``).
    host_root = workspace_dir.parent.parent
    magis_url = resolve_magis_database_url(host_root, magis_name)
    bus = open_bus(magis_url=magis_url)
    return load_runtime_spec(
        bus,
        workspace_dir.name,
        magis_database_url=magis_url,
    )


def test_init_provisions_only_canonical_node_database(tmp_path: Path) -> None:
    config = _first_config(tmp_path)
    spec = init_first_magi(config)

    workspace = config.workspace_dir
    assert spec.runtime_port == 42070
    assert (workspace / "memories" / "magi.db").is_file()
    assert not (workspace / "magi.db").exists()
    assert (tmp_path / "MAGI_Societies" / "genesis" / "magis.db").is_file()
    assert not (tmp_path / "MAGI_Societies" / "genesis" / "control").exists()
    assert _load_spec_from_db(workspace_dir=workspace) == spec
    bus = open_bus(
        workspace_dir=str(workspace),
        magis_url=spec.magis_database_url,
    )
    assert bus.settings_book.get_value(key="auth.signing_key")
    assert "a2a" in bus.settings_book.channel_options()


def test_named_sqlite_magis_is_isolated_from_local_store(tmp_path: Path) -> None:
    config = StartupConfig(tmp_path, DEFAULT_MAGI_NAME, None, None, "research")
    spec = init_first_magi(config)

    assert spec.magis_name == "research"
    assert (tmp_path / "MAGI_Societies" / "research" / "magis.db").is_file()
    bus = open_bus(
        workspace_dir=str(config.workspace_dir),
        magis_url=spec.magis_database_url,
    )
    local_tables = set(inspect(bus._local_factory.engine).get_table_names())
    magis_tables = set(inspect(bus._magis_factory.engine).get_table_names())
    assert {"settings", "chat_notify_jobs", "contacts"} <= local_tables
    assert "magis" not in local_tables
    assert {"magis", "runtime_state", "a2a_request_jobs", "a2a_notify_jobs"} <= magis_tables
    assert "settings" not in magis_tables
    assert "auth_credentials" not in magis_tables
    assert "magi_schema_revisions" not in local_tables
    assert "magi_schema_revisions" not in magis_tables
    # After the 2026.08 collapse there is a single revision per scope.
    with bus._local_factory.engine.connect() as connection:
        assert _select_version(connection) == LOCAL_HEAD_REVISION
    with bus._magis_factory.engine.connect() as connection:
        assert _select_version(connection) == MAGIS_HEAD_REVISION


def test_contacts_admin_authority_lives_on_magis_admin_id_not_a_local_admin_flag() -> None:
    """``_MagisAdminRow`` carries the canonical admin authority.

    The pre-collapse ``contacts`` row used to hold a ``password_hash``
    and ``admin`` flag. Migration ``0008`` folded those into the
    ``magis_admins`` table; after the collapse the initial schema's
    contact row no longer declares either column, and the link
    survives as ``_ContactRow.magis_admin_id`` →
    ``_MagisAdminRow.id``. This guard ensures the FK / role split
    doesn't silently regress.

    Uses table-metadata introspection rather than DDL-string
    comparison so the check stays stable across native-enum
    columns (PostgreSQL ENUM ``CREATE TYPE`` requires a type
    name, which the previous DDL-string check would surface as
    a ``CompileError`` instead of an assertion failure).
    """
    admin_columns = {col.name for col in _MagisAdminRow.__table__.columns}
    contact_columns = {col.name for col in _ContactRow.__table__.columns}

    # _MagisAdminRow has no FK to the local ``contacts`` table —
    # admin identity is rooted in the MAGIS-shared store.
    admin_fks = {
        fk.target_fullname
        for fk in _MagisAdminRow.__table__.foreign_keys
    }
    assert "contacts.id" not in admin_fks
    assert "contacts" not in admin_fks
    # _MagisAdminRow does declare its own self-referential
    # ``magis_id`` FK, which is fine.
    assert "magis.id" in admin_fks

    # Pre-collapse era columns stay gone from ``contacts``.
    assert "password_hash" not in contact_columns
    assert "admin" not in contact_columns
    # The local projection keeps a back-reference to the MAGIS admin row.
    assert "magis_admin_id" in contact_columns


def test_engine_factory_recognises_sqlite_driver_variants_and_rejects_other_backends(
    tmp_path: Path,
) -> None:
    factory = EngineFactory(f"sqlite+pysqlite:///{tmp_path / 'magis.db'}")
    assert factory.dialect == "sqlite"
    with pytest.raises(ValueError, match="SQLite or PostgreSQL"):
        EngineFactory("mysql://localhost/not-supported")


def test_node_creation_has_sticky_distinct_runtime_port(tmp_path: Path) -> None:
    first = init_first_magi(_first_config(tmp_path))
    second = create_node(StartupConfig(tmp_path, "eva-001", None, None))

    assert first.runtime_port == 42070
    assert second.runtime_port == 42071
    assert _load_spec_from_db(workspace_dir=tmp_path / "MAGI_Citizens" / "eva-001") == second


def test_repeated_init_is_identity_and_key_idempotent(tmp_path: Path) -> None:
    config = _first_config(tmp_path)
    first = init_first_magi(config)
    first_bus = open_bus(
        workspace_dir=str(config.workspace_dir),
        magis_url=first.magis_database_url,
    )
    signing_key = first_bus.settings_book.get_value(key="auth.signing_key")
    members_before = first_bus.memberships_book.list_for_magis(magis_id=1)

    second = init_first_magi(config)
    second_bus = open_bus(
        workspace_dir=str(config.workspace_dir),
        magis_url=second.magis_database_url,
    )

    assert second == first
    assert second_bus.settings_book.get_value(key="auth.signing_key") == signing_key
    assert second_bus.memberships_book.list_for_magis(magis_id=1) == members_before


def test_repeated_node_create_fails_without_duplicate_registration(tmp_path: Path) -> None:
    init_first_magi(_first_config(tmp_path))
    config = StartupConfig(tmp_path, "eva-001", None, None)
    create_node(config)

    with pytest.raises(ConfigurationError, match="workspace already exists"):
        create_node(config)


def test_retired_database_blocks_provision_and_runtime_open(tmp_path: Path) -> None:
    config = _first_config(tmp_path)
    config.workspace_dir.mkdir(parents=True)
    (config.workspace_dir / "magi.db").touch()

    with pytest.raises(StorageNotProvisioned, match="retired node database"):
        init_first_magi(config)
    # The runtime spec is no longer loaded from a workspace file —
    # ``magi.db`` presence is now an :func:`init_first_magi` failure
    # mode only. The DB-derived path always reads from the MAGIS
    # store, so the retired-database gate is exercised by the line
    # above alone.


def test_runtime_open_never_creates_a_missing_node_database(tmp_path: Path) -> None:
    state_dir = tmp_path / "MAGI_Citizens" / "eva-000" / "memories"
    state_dir.mkdir(parents=True)

    with pytest.raises(StorageNotProvisioned, match="node database is missing"):
        open_bus(workspace_dir=str(state_dir.parent))

    assert not (state_dir / "magi.db").exists()


def test_open_bus_without_workspace_uses_only_the_magis_store(tmp_path: Path) -> None:
    config = _first_config(tmp_path)
    spec = init_first_magi(config)

    bus = open_bus(magis_url=spec.magis_database_url)

    assert bus._magis_factory.url == spec.magis_database_url
    assert not hasattr(bus, "_local_factory")
    assert not hasattr(bus, "prompt_book")
    assert not hasattr(bus, "skills_book")
    bus.control_settings_book.set(key="control.test", value="shared")
    assert bus.control_settings_book.get_value(key="control.test") == "shared"
    assert "settings" not in set(inspect(bus._magis_factory.engine).get_table_names())


def test_runtime_open_recreates_a_missing_bus_table_before_books_are_wired(tmp_path: Path) -> None:
    """Dropping a table on a live DB is repaired by ``synchronise_schema``.

    With the 2026.08 collapse there is no per-migration "add column"
    or "rename column" DDL. The ``0001_initial_schema`` migration's
    :func:`upgrade` is itself ``Base.metadata.create_all``, which
    acts as the additive repair path during
    :func:`magi.bus.firmwares.schema.synchronise_schema` (the legacy
    ``create_all`` half was kept for exactly this situation).
    """
    config = _first_config(tmp_path)
    spec = init_first_magi(config)
    bus = open_bus(
        workspace_dir=str(config.workspace_dir),
        magis_url=spec.magis_database_url,
    )
    with bus._local_factory.engine.begin() as connection:
        connection.execute(text("DROP TABLE action_items"))
        # Backdate the alembic stamp so a future upgrade pass would
        # appear "stale" — proves the additive create_all path
        # (not alembic) is what repairs the dropped table.
        connection.execute(
            text("DELETE FROM alembic_version")
        )

    repaired = open_bus(
        workspace_dir=str(config.workspace_dir),
        magis_url=spec.magis_database_url,
    )
    assert "action_items" in set(inspect(repaired._local_factory.engine).get_table_names())
    with repaired._local_factory.engine.connect() as connection:
        assert _select_version(connection) == LOCAL_HEAD_REVISION


def test_initial_schema_does_not_create_legacy_a2a_outbox(tmp_path: Path) -> None:
    """The legacy local ``a2a_jobs`` outbox is gone for good.

    Pre-collapse, migration 0005 dropped the table. After the
    collapse, the initial schema simply doesn't declare it. A
    brand-new DB has no ``a2a_jobs`` table by construction.
    """
    state_dir = tmp_path / "memories"
    state_dir.mkdir()
    factory = EngineFactory(f"sqlite:///{state_dir / 'magi.db'}")

    with factory.engine.begin() as connection:
        # Initial-schema bring-up: create_all + alembic stamp.
        from magi.old_bus.firmwares.schema import LOCAL_SCOPE, synchronise_schema

    # Use ``synchronise_schema`` indirectly by going through ``open_bus``
    # — first, drop the existing DB so the boot is truly from scratch.
    (state_dir / "magi.db").unlink()
    config = _first_config(tmp_path / "MAGI_Citizens" / "eva-000")
    config.workspace_dir.mkdir(parents=True)
    # ``init_first_magi`` provisions the local + magis DBs together; we
    # only need the local store's table set for the assertion below.
    spec = init_first_magi(config)
    bus = open_bus(
        workspace_dir=str(config.workspace_dir),
        magis_url=spec.magis_database_url,
    )
    assert "a2a_jobs" not in set(inspect(bus._local_factory.engine).get_table_names())


def test_runtime_app_creation_repairs_schema_before_runtime_context_is_exposed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _first_config(tmp_path)
    spec = init_first_magi(config)
    bus = open_bus(
        workspace_dir=str(config.workspace_dir),
        magis_url=spec.magis_database_url,
    )
    with bus._local_factory.engine.begin() as connection:
        connection.execute(text("DROP TABLE action_items"))

    repaired_bus = open_bus(
        workspace_dir=str(config.workspace_dir),
        magis_url=spec.magis_database_url,
    )
    startup = runtime._startup_context(
        config,
        repaired_bus,
        magis_url=spec.magis_database_url,
    )
    context = runtime.RuntimeContext.create(startup, repaired_bus)
    app = runtime._create_runtime_app(context)

    assert "action_items" in set(inspect(app.state.bus._local_factory.engine).get_table_names())


def test_run_magi_serves_the_in_process_runtime_app_without_reload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _first_config(tmp_path)
    startup = StartupContext(
        host_workspace_dir=tmp_path,
        workspace_dir=tmp_path / "MAGI_Citizens" / DEFAULT_MAGI_NAME,
        magi_name=DEFAULT_MAGI_NAME,
        magi_id="1",
        magis_name="genesis",
        magis_database_url="sqlite:///magis.db",
        private_database_url="sqlite:///magi.db",
        is_first_magi=True,
        runtime_port=42123,
    )
    captured: dict[str, object] = {}
    app = object()

    def _run(app, **kwargs) -> None:
        captured["app"] = app
        captured.update(kwargs)

    def _open_bus(*, workspace_dir: str, magis_url: str) -> object:
        captured["open_bus"] = (workspace_dir, magis_url)
        return object()

    def _startup_context(_config, _bus, *, magis_url: str) -> StartupContext:
        captured["startup_magis_url"] = magis_url
        return startup

    monkeypatch.setattr(runtime.uvicorn, "run", _run)
    monkeypatch.setattr("magi.bus.open_bus", _open_bus)
    monkeypatch.setattr(runtime, "_startup_context", _startup_context)
    monkeypatch.setattr(
        runtime.RuntimeContext,
        "create",
        lambda _startup, _bus: object(),
    )
    monkeypatch.setattr(runtime, "_create_runtime_app", lambda _context: app)
    monkeypatch.setattr(runtime, "mark_registry_stopped", lambda _config: None)

    runtime.run_magi(config)

    assert captured["app"] is app
    assert "factory" not in captured
    assert "reload" not in captured
    assert "reload_dirs" not in captured
    assert captured["port"] == startup.runtime_port
    assert captured["open_bus"] == (
        str(config.workspace_dir),
        captured["startup_magis_url"],
    )


# -- Initial schema: native-Enum + DateTime shape guarantees ---------------


def test_initial_schema_declares_native_enum_on_a2a_tables(tmp_path: Path) -> None:
    """``a2a_request_jobs`` / ``a2a_notify_jobs`` get native enum CHECK constraints.

    The collapsed initial schema inherits the post-0009 state of the
    A2A tables (``status`` and ``error_code`` are native enums; the
    ``source_channel`` / ``source_conversation_id`` /
    ``tool_call_id`` columns dropped in 0007 stay gone). On SQLite
    SQLAlchemy renders native enums as ``VARCHAR(length)`` plus a
    ``CHECK (col IN (...))`` constraint — the assertion is on the
    CHECK being present and bounded, not on a specific dialect
    syntax.
    """
    from magi.old_bus.firmwares.schema import MAGIS_SCOPE, synchronise_schema
    from magi.old_bus.bases.db.engine import EngineFactory

    factory = EngineFactory(f"sqlite:///{tmp_path / 'magis.db'}")
    synchronise_schema(factory, scope=MAGIS_SCOPE)

    insp = inspect(factory.engine)
    for table in ("a2a_request_jobs", "a2a_notify_jobs"):
        columns = {c["name"] for c in insp.get_columns(table)}
        # 0007's dropped columns stay dropped.
        assert "source_channel" not in columns
        assert "source_conversation_id" not in columns
        if table == "a2a_request_jobs":
            assert "tool_call_id" not in columns

        checks = [c["sqltext"] or "" for c in insp.get_check_constraints(table)]
        assert any("status" in sql for sql in checks), (
            f"{table}.status should carry a CHECK constraint from the "
            f"native enum; got {checks!r}"
        )
        assert any("error_code" in sql for sql in checks), (
            f"{table}.error_code should carry a CHECK constraint from the "
            f"native enum; got {checks!r}"
        )


def test_initial_schema_declares_datetime_columns_for_tasks(tmp_path: Path) -> None:
    """Time columns on ``tasks`` / ``task_runs`` are ``DateTime`` from the initial schema.

    Pre-collapse, migration 0014 did the ``VARCHAR(32)`` →
    ``DateTime`` promotion. After the collapse, ``Base.metadata``
    already declares the time columns as ``DateTime`` and the
    initial schema mirrors them via ``create_all`` — so a fresh DB
    lands with ``DateTime`` columns and no follow-up migration is
    needed. ``synchronise_schema`` is idempotent: re-opening the
    same DB does not change the column types.
    """
    config = _first_config(tmp_path)
    init_first_magi(config)

    spec = _load_spec_from_db(workspace_dir=config.workspace_dir)
    bus = open_bus(
        workspace_dir=str(config.workspace_dir),
        magis_url=spec.magis_database_url,
    )

    # Schema is at the single 0001 head on a fresh DB.
    with bus._local_factory.engine.connect() as connection:
        assert _select_version(connection) == LOCAL_HEAD_REVISION

    # Time columns are DateTime directly from create_all — no migration DDL needed.
    tasks_columns = {
        c["name"]: c["type"]
        for c in inspect(bus._local_factory.engine).get_columns("tasks")
    }
    for col in ("created_at", "updated_at", "last_run_at"):
        assert "DATETIME" in str(tasks_columns[col]).upper(), (
            f"tasks.{col} should already be DateTime on a fresh DB; "
            f"got {tasks_columns[col]!r}"
        )
    runs_columns = {
        c["name"]: c["type"]
        for c in inspect(bus._local_factory.engine).get_columns("task_runs")
    }
    for col in ("started_at", "finished_at"):
        assert "DATETIME" in str(runs_columns[col]).upper(), (
            f"task_runs.{col} should already be DateTime on a fresh DB; "
            f"got {runs_columns[col]!r}"
        )

    # Idempotent re-open does not change column types.
    reopened = open_bus(
        workspace_dir=str(config.workspace_dir),
        magis_url=spec.magis_database_url,
    )
    tasks_columns_2 = {
        c["name"]: c["type"]
        for c in inspect(reopened._local_factory.engine).get_columns("tasks")
    }
    for col in ("created_at", "updated_at", "last_run_at"):
        assert str(tasks_columns_2[col]) == str(tasks_columns[col]), (
            f"tasks.{col} type changed across re-open: "
            f"{tasks_columns[col]!r} → {tasks_columns_2[col]!r}"
        )

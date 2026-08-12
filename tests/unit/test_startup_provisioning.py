"""Regression coverage for explicit MAGI provisioning and runtime opening."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from magi.bus import open_bus, open_control_bus
from magi.bus.db.engine import EngineFactory
from magi.bus.library.local.contactBook import _ContactRow
from magi.bus.library.magis.magisBook import _MagisAdminRow
from magi.bus.provision import StorageNotProvisioned
from magi.startup import runtime
from magi.startup.config import DEFAULT_MAGI_NAME, ConfigurationError, StartupConfig
from magi.startup.provision import create_node, init_first_magi
from magi.startup.spec import load_runtime_spec


def _first_config(root: Path) -> StartupConfig:
    return StartupConfig(root, DEFAULT_MAGI_NAME, None, None)


def test_init_provisions_only_canonical_node_database(tmp_path: Path) -> None:
    config = _first_config(tmp_path)
    spec = init_first_magi(config)

    workspace = config.workspace_dir
    assert spec.runtime_port == 42070
    assert (workspace / "memories" / "magi.db").is_file()
    assert not (workspace / "magi.db").exists()
    assert (tmp_path / "MAGI_Societies" / "genesis" / "magis.db").is_file()
    assert load_runtime_spec(workspace) == spec
    assert open_bus(
        state_dir=str(workspace / "memories"),
        magis_url=spec.magis_database_url,
    ).settings_book.get(key="auth.signing_key")


def test_named_sqlite_magis_is_isolated_from_local_store(tmp_path: Path) -> None:
    config = StartupConfig(tmp_path, DEFAULT_MAGI_NAME, None, None, "research")
    spec = init_first_magi(config)

    assert spec.magis_name == "research"
    assert (tmp_path / "MAGI_Societies" / "research" / "magis.db").is_file()
    bus = open_bus(
        state_dir=str(config.workspace_dir / "memories"),
        magis_url=spec.magis_database_url,
    )
    local_tables = set(inspect(bus._local_factory.engine).get_table_names())
    magis_tables = set(inspect(bus._magis_factory.engine).get_table_names())
    assert {"settings", "chat_jobs", "contacts"} <= local_tables
    assert "magis" not in local_tables
    assert {"magis", "runtime_state", "a2a_request_jobs", "a2a_notify_jobs"} <= magis_tables
    assert "settings" not in magis_tables
    assert "auth_credentials" not in magis_tables
    assert "magi_schema_revisions" not in local_tables
    assert "magi_schema_revisions" not in magis_tables
    with bus._local_factory.engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0014_convert_task_time_columns_to_datetime"
        )
    with bus._magis_factory.engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0005_split_a2a_notify_payload_into_source_columns"
        )


def test_password_hash_is_local_to_contacts_not_magis() -> None:
    dialect = postgresql.dialect()
    admin_ddl = str(CreateTable(_MagisAdminRow.__table__).compile(dialect=dialect))
    contact_ddl = str(CreateTable(_ContactRow.__table__).compile(dialect=dialect))

    assert "REFERENCES contacts" not in admin_ddl
    assert "password_hash" in contact_ddl


def test_open_bus_upgrades_existing_local_contacts_with_password_hash(tmp_path: Path) -> None:
    state_dir = tmp_path / "memories"
    state_dir.mkdir()
    database_url = f"sqlite:///{state_dir / 'magi.db'}"
    factory = EngineFactory(database_url)
    with factory.engine.begin() as connection:
        connection.execute(
            text("""
            CREATE TABLE contacts (
                id INTEGER PRIMARY KEY,
                name VARCHAR(120) NOT NULL,
                display_name VARCHAR(120),
                role VARCHAR(16) NOT NULL,
                telegram_id BIGINT,
                admin BOOLEAN NOT NULL,
                last_seen_at DATETIME NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
        """)
        )
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(
            text(
                "INSERT INTO alembic_version (version_num) VALUES ('0003_rename_a2a_invocation_id_and_table')"
            )
        )

    bus = open_bus(state_dir=str(state_dir))

    assert "password_hash" in {
        column["name"] for column in inspect(bus._local_factory.engine).get_columns("contacts")
    }
    with bus._local_factory.engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0014_convert_task_time_columns_to_datetime"
        )


def test_open_bus_renames_legacy_contacts_telegram_id_to_tgid(tmp_path: Path) -> None:
    """``0006`` renames the column in place, preserving bound chat ids.

    Guards two things at once: that an existing deployment's
    ``contacts.telegram_id`` survives the rename with its data
    (a ``RENAME COLUMN``, not a drop-and-add), and that the
    guarded migration is safe to re-run — ``upgrade_schema``
    executes on every BUS open, not just once.
    """
    state_dir = tmp_path / "memories"
    state_dir.mkdir()
    factory = EngineFactory(f"sqlite:///{state_dir / 'magi.db'}")
    with factory.engine.begin() as connection:
        connection.execute(
            text("""
            CREATE TABLE contacts (
                id INTEGER PRIMARY KEY,
                name VARCHAR(120) NOT NULL,
                display_name VARCHAR(120),
                role VARCHAR(16) NOT NULL,
                telegram_id BIGINT,
                admin BOOLEAN NOT NULL,
                last_seen_at DATETIME NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                password_hash VARCHAR(255)
            )
        """)
        )
        connection.execute(
            text("""
            INSERT INTO contacts
                (id, name, role, telegram_id, admin,
                 last_seen_at, created_at, updated_at)
            VALUES (1, 'Alice', 'assigned', 12345, 0,
                    '2026-01-01 00:00:00', '2026-01-01 00:00:00', '2026-01-01 00:00:00')
        """)
        )
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(
            text(
                "INSERT INTO alembic_version (version_num)"
                " VALUES ('0005_drop_legacy_local_a2a_jobs')"
            )
        )

    bus = open_bus(state_dir=str(state_dir))

    columns = {
        column["name"] for column in inspect(bus._local_factory.engine).get_columns("contacts")
    }
    assert "tgid" in columns
    assert "telegram_id" not in columns
    assert bus.contacts_book.get_by_telegram(tgid=12345) is not None

    # Re-opening runs ``upgrade_schema`` again; the guard must make it a no-op.
    reopened = open_bus(state_dir=str(state_dir))
    assert reopened.contacts_book.get_by_telegram(tgid=12345) is not None


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
    assert load_runtime_spec(tmp_path / "MAGI_Citizens" / "eva-001") == second


def test_repeated_init_is_identity_and_key_idempotent(tmp_path: Path) -> None:
    config = _first_config(tmp_path)
    first = init_first_magi(config)
    first_bus = open_bus(
        state_dir=str(config.workspace_dir / "memories"),
        magis_url=first.magis_database_url,
    )
    signing_key = first_bus.settings_book.get(key="auth.signing_key")
    members_before = first_bus.memberships_book.list_for_magis(magis_id=1)

    second = init_first_magi(config)
    second_bus = open_bus(
        state_dir=str(config.workspace_dir / "memories"),
        magis_url=second.magis_database_url,
    )

    assert second == first
    assert second_bus.settings_book.get(key="auth.signing_key") == signing_key
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
    with pytest.raises(ConfigurationError, match="retired node database"):
        load_runtime_spec(config.workspace_dir)


def test_runtime_open_never_creates_a_missing_node_database(tmp_path: Path) -> None:
    state_dir = tmp_path / "MAGI_Citizens" / "eva-000" / "memories"
    state_dir.mkdir(parents=True)

    with pytest.raises(StorageNotProvisioned, match="node database is missing"):
        open_bus(state_dir=str(state_dir))

    assert not (state_dir / "magi.db").exists()


def test_control_bus_uses_magis_store_without_opening_node_store(tmp_path: Path) -> None:
    config = _first_config(tmp_path)
    spec = init_first_magi(config)
    control_dir = tmp_path / "MAGI_Societies" / "genesis" / "control"

    bus = open_control_bus(control_dir=str(control_dir), magis_url=spec.magis_database_url)

    assert bus._local_factory.url == spec.magis_database_url
    assert bus._magis_factory is not None
    # The per-MAGIS control directory is provisioned with its control secret;
    # opening the control BUS must not touch the node-private database.
    assert control_dir.is_dir()
    assert bus.control_settings_book is not None
    bus.control_settings_book.set(key="control.test", value="shared")
    assert bus.control_settings_book.get(key="control.test") == "shared"
    assert "settings" not in set(inspect(bus._magis_factory.engine).get_table_names())


def test_runtime_open_repairs_an_outdated_schema_before_exposing_books(tmp_path: Path) -> None:
    config = _first_config(tmp_path)
    spec = init_first_magi(config)
    bus = open_bus(
        state_dir=str(config.workspace_dir / "memories"),
        magis_url=spec.magis_database_url,
    )
    with bus._local_factory.engine.begin() as connection:
        connection.execute(text("ALTER TABLE contacts DROP COLUMN password_hash"))
        connection.execute(
            text(
                "UPDATE alembic_version SET version_num = '0003_rename_a2a_invocation_id_and_table'"
            )
        )

    repaired = open_bus(
        state_dir=str(config.workspace_dir / "memories"),
        magis_url=spec.magis_database_url,
    )
    assert "password_hash" in {
        column["name"] for column in inspect(repaired._local_factory.engine).get_columns("contacts")
    }
    with repaired._local_factory.engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0014_convert_task_time_columns_to_datetime"
        )


def test_runtime_open_drops_the_retired_local_a2a_outbox(tmp_path: Path) -> None:
    config = _first_config(tmp_path)
    spec = init_first_magi(config)
    bus = open_bus(
        state_dir=str(config.workspace_dir / "memories"),
        magis_url=spec.magis_database_url,
    )
    with bus._local_factory.engine.begin() as connection:
        connection.execute(text("CREATE TABLE a2a_jobs (id INTEGER PRIMARY KEY)"))
        connection.execute(
            text("UPDATE alembic_version SET version_num = '0004_add_contact_password_hash'")
        )

    repaired = open_bus(
        state_dir=str(config.workspace_dir / "memories"),
        magis_url=spec.magis_database_url,
    )
    assert "a2a_jobs" not in set(inspect(repaired._local_factory.engine).get_table_names())


def test_runtime_open_recreates_a_missing_bus_table_before_books_are_wired(tmp_path: Path) -> None:
    config = _first_config(tmp_path)
    spec = init_first_magi(config)
    bus = open_bus(
        state_dir=str(config.workspace_dir / "memories"),
        magis_url=spec.magis_database_url,
    )
    with bus._local_factory.engine.begin() as connection:
        connection.execute(text("DROP TABLE action_items"))

    repaired = open_bus(
        state_dir=str(config.workspace_dir / "memories"),
        magis_url=spec.magis_database_url,
    )
    assert "action_items" in set(inspect(repaired._local_factory.engine).get_table_names())


def test_reload_app_factory_repairs_schema_before_runtime_context_is_exposed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _first_config(tmp_path)
    spec = init_first_magi(config)
    bus = open_bus(
        state_dir=str(config.workspace_dir / "memories"),
        magis_url=spec.magis_database_url,
    )
    with bus._local_factory.engine.begin() as connection:
        connection.execute(text("DROP TABLE action_items"))

    runtime._publish_runtime_config(config)
    app = runtime.create_runtime_app_from_environment()

    assert "action_items" in set(inspect(app.state.bus._local_factory.engine).get_table_names())


def test_run_magi_uses_import_factory_for_uvicorn_reload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _first_config(tmp_path)
    spec = init_first_magi(config)
    captured: dict[str, object] = {}

    def _run(app, **kwargs) -> None:
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr(runtime.uvicorn, "run", _run)
    monkeypatch.setattr(runtime, "_reload_enabled", lambda: True)

    runtime.run_magi(config)

    assert captured["app"] == "magi.startup.runtime:create_runtime_app_from_environment"
    assert captured["factory"] is True
    assert captured["reload"] is True
    assert captured["port"] == spec.runtime_port


# -- 0014: tasks / task_runs time columns String(32) → DateTime ---------


def test_0014_converts_task_time_columns_and_preserves_task_runs(tmp_path: Path) -> None:
    """Migration ``0014_convert_task_time_columns_to_datetime``.

    Validates the four guarantees the migration must hold:

    1. ``tasks.{created_at, updated_at, last_run_at}`` are stored as
       native ``DateTime`` after upgrade (not ``VARCHAR(32)``).
    2. ``task_runs.{started_at, finished_at}`` are ``DateTime``
       after upgrade.
    3. Existing ``task_runs`` rows survive the column swap — the
       ``PRAGMA foreign_keys=OFF`` guard prevents SQLite from
       cascading the parent ``tasks`` recreation (which would
       otherwise wipe every child row).
    4. Legacy ISO strings parse to naive UTC datetimes — both
       naive-with-microseconds (the historical writer's output) and
       Z-suffixed (the alternative form inherited from
       ``validate_run_at``).
    """
    state_dir = tmp_path / "memories"
    state_dir.mkdir()
    factory = EngineFactory(f"sqlite:///{state_dir / 'magi.db'}")
    with factory.engine.begin() as connection:
        # Legacy schema at 0013 — time columns are VARCHAR(32) ISO.
        connection.execute(
            text(
                """
                CREATE TABLE tasks (
                    id VARCHAR(26) PRIMARY KEY,
                    name VARCHAR(120) NOT NULL,
                    prompt TEXT NOT NULL,
                    source VARCHAR(16) NOT NULL,
                    channel VARCHAR(16) NOT NULL,
                    enabled INTEGER NOT NULL,
                    cron VARCHAR(120),
                    run_at VARCHAR(32),
                    tz VARCHAR(64) NOT NULL,
                    delivery_to VARCHAR(128),
                    conversation_id VARCHAR(26),
                    contact_id INTEGER,
                    consecutive_failures INTEGER NOT NULL,
                    last_run_at VARCHAR(32),
                    last_status VARCHAR(16),
                    last_error VARCHAR(500),
                    created_at VARCHAR(32) NOT NULL,
                    updated_at VARCHAR(32) NOT NULL
                )
            """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE task_runs (
                    id VARCHAR(26) PRIMARY KEY,
                    task_id VARCHAR(26) NOT NULL
                        REFERENCES tasks(id) ON DELETE CASCADE,
                    conversation_id VARCHAR(26),
                    manual INTEGER NOT NULL DEFAULT 0,
                    started_at VARCHAR(32) NOT NULL,
                    finished_at VARCHAR(32),
                    latency_ms INTEGER,
                    status VARCHAR(16) NOT NULL,
                    error VARCHAR(500),
                    reply_excerpt VARCHAR(500)
                )
            """
            )
        )
        # Mixed-form ISO data: naive microseconds + Z-suffixed.
        connection.execute(
            text(
                """
                INSERT INTO tasks (
                    id, name, prompt, source, channel, enabled,
                    cron, run_at, tz, delivery_to, conversation_id,
                    contact_id, consecutive_failures,
                    last_run_at, last_status, last_error,
                    created_at, updated_at
                ) VALUES (
                    'task_001', 'Daily brief', 'summarise', 'user',
                    'webui', 1, '0 9 * * *', NULL, 'UTC', NULL,
                    NULL, NULL, 0,
                    '2026-08-01T13:00:00Z',
                    NULL, NULL,
                    '2026-08-01T12:00:00.123456',
                    '2026-08-01T12:00:00.123456'
                )
            """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO task_runs (
                    id, task_id, conversation_id, manual,
                    started_at, finished_at, latency_ms,
                    status, error, reply_excerpt
                ) VALUES (
                    'run_001', 'task_001', NULL, 0,
                    '2026-08-01T12:30:00.123456',
                    '2026-08-01T12:31:00.500000',
                    NULL,
                    'success', NULL, NULL
                )
            """
            )
        )
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        connection.execute(
            text(
                "INSERT INTO alembic_version (version_num) "
                "VALUES ('0013_replace_run_task_fired_by_and_task_run_trigger')"
            )
        )

    bus = open_bus(state_dir=str(state_dir))

    # 1. Column types are DateTime on both tables.
    tasks_columns = {
        c["name"]: c["type"]
        for c in inspect(bus._local_factory.engine).get_columns("tasks")
    }
    for col in ("created_at", "updated_at", "last_run_at"):
        assert col in tasks_columns, f"tasks.{col} missing"
        assert "DATETIME" in str(tasks_columns[col]).upper(), (
            f"tasks.{col} should be DateTime, got {tasks_columns[col]!r}"
        )
    runs_columns = {
        c["name"]: c["type"]
        for c in inspect(bus._local_factory.engine).get_columns("task_runs")
    }
    for col in ("started_at", "finished_at"):
        assert col in runs_columns, f"task_runs.{col} missing"
        assert "DATETIME" in str(runs_columns[col]).upper(), (
            f"task_runs.{col} should be DateTime, got {runs_columns[col]!r}"
        )

    # 2. Schema is at head (0014).
    with bus._local_factory.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == "0014_convert_task_time_columns_to_datetime"
        )

    # 3. Existing task_runs rows survived the column swap — no FK cascade wipe.
    with bus._local_factory.engine.connect() as connection:
        rows = connection.execute(text("SELECT id FROM task_runs")).fetchall()
        assert [r[0] for r in rows] == ["run_001"]

    # 4. Legacy ISO strings parsed into naive UTC datetimes — validated
    #    through the Book so the assertion exercises the full
    #    ORM → ``datetime`` round-trip (not just SQLite's text fallback
    #    that raw ``SELECT`` returns for DateTime columns).
    from magi.bus.library.local.tasksBook import TaskBook, TaskRunBook

    task = TaskBook(bus._local_factory).get(task_id="task_001")
    assert task is not None
    # naive microseconds pass through unchanged.
    assert task.created_at is not None
    assert task.created_at.year == 2026
    assert task.created_at.month == 8
    assert task.created_at.day == 1
    assert task.created_at.microsecond == 123456
    assert task.created_at.tzinfo is None
    # Z-suffixed → astimezone(UTC) → naive UTC.
    assert task.last_run_at is not None
    assert task.last_run_at.hour == 13
    assert task.last_run_at.minute == 0
    assert task.last_run_at.tzinfo is None

    run = TaskRunBook(bus._local_factory).get(id="run_001")
    assert run is not None
    assert run.started_at.minute == 30
    assert run.started_at.microsecond == 123456
    assert run.finished_at is not None
    assert run.finished_at.minute == 31
    assert run.finished_at.microsecond == 500000
    # status is the loose String column that 0014 doesn't touch.
    assert run.status.value == "success"


def test_0014_is_a_noop_on_fresh_db(tmp_path: Path) -> None:
    """A fresh DB lands at 0014 with ``DateTime`` columns straight from ``create_all``.

    Validates the migration's idempotency guard: the upgrade must
    no-op when the column is already ``DateTime`` (which is the
    shape that ``Base.metadata.create_all`` produces when the
    Python ORM is ahead of the migration, which is what happens
    on a fresh deployment).
    """
    config = _first_config(tmp_path)
    init_first_magi(config)

    spec = load_runtime_spec(config.workspace_dir)
    bus = open_bus(
        state_dir=str(config.workspace_dir / "memories"),
        magis_url=spec.magis_database_url,
    )

    # Schema is at 0014 head on a fresh DB.
    with bus._local_factory.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == "0014_convert_task_time_columns_to_datetime"
        )

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

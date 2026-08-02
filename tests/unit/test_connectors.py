"""Unit tests for magi.connectors — base, bus, registry, calendar sample."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from magi.connectors import (
    Connector,
    ConnectorConfig,
    ConnectorEvent,
    ConnectorEventKind,
    EventBus,
    get_bus,
    get_connector,
    list_connectors,
    load_connectors,
    publish,
    register_connector,
    register_connector_factory,
    reset_bus,
    reset_registry,
    unload_all,
)
from magi.connectors.registry import get_factory
from magi.connectors.samples.calendar import (
    CalendarConnector,
    install_calendar_connector,
)


@pytest.fixture(autouse=True)
def _reset_buses():
    """Each test starts with a fresh bus + registry."""
    reset_bus()
    reset_registry()
    yield
    reset_bus()
    reset_registry()


# -- ConnectorEvent / ConnectorConfig --------------------------------------


def test_connector_event_key_is_stable():
    a = ConnectorEvent(
        connector="calendar", kind=ConnectorEventKind.CREATED,
        id="ev-1", payload={"x": 1},
    )
    b = ConnectorEvent(
        connector="calendar", kind=ConnectorEventKind.CREATED,
        id="ev-1", payload={"x": 999},
    )
    assert a.key() == b.key() == ("calendar", "ev-1")


def test_connector_config_key_includes_instance_id():
    cfg_a = ConnectorConfig(name="calendar", instance_id="alice")
    cfg_b = ConnectorConfig(name="calendar", instance_id="bob")
    assert cfg_a.key() != cfg_b.key()


# -- EventBus ---------------------------------------------------------------


def test_eventbus_publish_fanout():
    bus = EventBus()
    received: list[ConnectorEvent] = []

    async def handler(ev):
        received.append(ev)

    bus.subscribe(ConnectorEventKind.CREATED.value, handler)
    asyncio.run(bus.publish(
        ConnectorEvent(
            connector="cal", kind=ConnectorEventKind.CREATED,
            id="1", payload={},
        ),
    ))
    asyncio.run(asyncio.sleep(0))  # let scheduled tasks drain
    assert len(received) == 1


def test_eventbus_dedup_within_window():
    bus = EventBus(dedup_window_seconds=60.0)
    received: list[str] = []

    async def handler(ev):
        received.append(ev.id)

    bus.subscribe(ConnectorEventKind.CREATED.value, handler)

    async def go():
        for _ in range(3):
            await bus.publish(
                ConnectorEvent(
                    connector="cal", kind=ConnectorEventKind.CREATED,
                    id="same", payload={},
                ),
            )

    asyncio.run(go())
    asyncio.run(asyncio.sleep(0))
    assert received == ["same"]  # only the first goes through


def test_eventbus_unsubscribe_is_noop_when_unknown():
    bus = EventBus()

    async def handler(ev):
        pass

    bus.unsubscribe(ConnectorEventKind.CREATED.value, handler)


def test_handler_exceptions_do_not_break_bus():
    bus = EventBus()
    ok: list[str] = []

    async def bad(ev):
        raise RuntimeError("nope")

    async def good(ev):
        ok.append(ev.id)

    bus.subscribe(ConnectorEventKind.CREATED.value, bad)
    bus.subscribe(ConnectorEventKind.CREATED.value, good)

    async def go():
        await bus.publish(
            ConnectorEvent(
                connector="x", kind=ConnectorEventKind.CREATED,
                id="1", payload={},
            ),
        )

    asyncio.run(go())
    asyncio.run(asyncio.sleep(0))
    assert ok == ["1"]


# -- Registry ---------------------------------------------------------------


def test_register_and_get_connector():
    cfg = ConnectorConfig(name="calendar", instance_id="alice")
    connector = CalendarConnector(cfg)
    register_connector(connector, instance_id="alice")

    assert get_connector("calendar", "alice") is connector
    assert get_connector("calendar", "missing") is None
    assert ("calendar", "alice") in list_connectors()


def test_register_connector_factory_is_idempotent():
    register_connector_factory("calendar", lambda c: CalendarConnector(c))
    factory1 = get_factory("calendar")
    register_connector_factory("calendar", lambda c: CalendarConnector(c))
    factory2 = get_factory("calendar")
    # second call replaced the prior factory — they are
    # different objects
    assert factory1 is not factory2


def test_load_connectors_skips_disabled():
    register_connector_factory("calendar", lambda c: CalendarConnector(c))

    async def go():
        return await load_connectors([
            ConnectorConfig(name="calendar", instance_id="on", enabled=True),
            ConnectorConfig(name="calendar", instance_id="off", enabled=False),
        ])

    loaded = asyncio.run(go())
    # ``enabled=False`` rows are filtered inside load_connectors
    assert len(loaded) == 1
    assert get_connector("calendar", "on") is not None
    assert get_connector("calendar", "off") is None


def test_load_connectors_skips_unknown_factory():
    async def go():
        return await load_connectors([
            ConnectorConfig(name="nonexistent", instance_id="x", enabled=True),
        ])

    loaded = asyncio.run(go())
    assert loaded == []


def test_load_connectors_replaces_existing():
    register_connector_factory("calendar", lambda c: CalendarConnector(c))

    async def go():
        await load_connectors([
            ConnectorConfig(name="calendar", instance_id="dup", enabled=True),
        ])
        first = get_connector("calendar", "dup")
        await load_connectors([
            ConnectorConfig(name="calendar", instance_id="dup", enabled=True),
        ])
        second = get_connector("calendar", "dup")
        return first, second

    first, second = asyncio.run(go())
    assert first is not None
    assert second is not None
    # replaced — different instances
    assert first is not second


def test_unload_all_disconnects_everything():
    register_connector_factory("calendar", lambda c: CalendarConnector(c))

    async def go():
        await load_connectors([
            ConnectorConfig(name="calendar", instance_id="a", enabled=True),
            ConnectorConfig(name="calendar", instance_id="b", enabled=True),
        ])
        await unload_all()
        return list_connectors()

    assert asyncio.run(go()) == []


# -- Calendar sample (ical file path) ---------------------------------------


def test_calendar_install_registers_factory():
    install_calendar_connector()  # idempotent
    from magi.connectors.registry import get_factory
    assert get_factory("calendar") is not None


def test_calendar_fetch_requires_iso_window(tmp_path: Path):
    cfg = ConnectorConfig(
        name="calendar", instance_id="t",
        enabled=True,
        settings={"source": "ical", "path": str(tmp_path / "x.ics")},
    )
    c = CalendarConnector(cfg)
    result = asyncio.run(c.fetch({}))
    assert result["events"] == []
    assert "error" in result


def test_calendar_fetch_ical(tmp_path: Path):
    ical = (
        "BEGIN:VCALENDAR\n"
        "BEGIN:VEVENT\n"
        "UID:ev-1\n"
        "DTSTART:20260601T100000Z\n"
        "DTEND:20260601T110000Z\n"
        "SUMMARY:Standup\n"
        "LOCATION:Room 1\n"
        "END:VEVENT\n"
        "END:VCALENDAR\n"
    )
    p = tmp_path / "cal.ics"
    p.write_text(ical)

    cfg = ConnectorConfig(
        name="calendar", instance_id="t", enabled=True,
        settings={"source": "ical", "path": str(p)},
    )
    c = CalendarConnector(cfg)
    result = asyncio.run(c.fetch({
        "from": "2026-06-01T00:00:00+00:00",
        "to": "2026-06-02T00:00:00+00:00",
    }))
    assert len(result["events"]) == 1
    ev = result["events"][0]
    assert ev["id"] == "ev-1"
    assert ev["title"] == "Standup"
    assert ev["location"] == "Room 1"


def test_calendar_probe_rejects_missing_ical(tmp_path: Path):
    cfg = ConnectorConfig(
        name="calendar", instance_id="t", enabled=True,
        settings={"source": "ical", "path": str(tmp_path / "missing.ics")},
    )
    c = CalendarConnector(cfg)
    with pytest.raises(FileNotFoundError):
        asyncio.run(c._probe())


def test_calendar_probe_rejects_unknown_source():
    cfg = ConnectorConfig(
        name="calendar", instance_id="t", enabled=True,
        settings={"source": "bogus"},
    )
    c = CalendarConnector(cfg)
    with pytest.raises(ValueError):
        asyncio.run(c._probe())
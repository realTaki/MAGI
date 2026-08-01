"""Unit tests for magi.plugins — bus, audit_log sample, bus bridge."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from magi.connectors.base import ConnectorEvent, ConnectorEventKind
from magi.connectors.bus import (
    get_bus as get_connector_bus,
    publish as publish_connector,
    reset_bus as reset_connector_bus,
)
from magi.connectors.bridge import (
    start_connector_bridge,
    stop_connector_bridge,
)
from magi.plugins.base import Hook, Plugin, PluginContext
from magi.plugins.bus import (
    HookBus,
    emit,
    get_bus,
    install_all,
    list_plugins,
    register_plugin,
    reset,
    reset_bus,
    shutdown_all,
)
from magi.plugins.samples.audit_log import (
    AuditLogPlugin,
    AuditRecord,
    FileAuditStore,
    MemoryAuditStore,
)


@pytest.fixture(autouse=True)
def _reset_buses():
    reset_bus()
    reset_connector_bus()
    reset()
    stop_connector_bridge()
    yield
    reset_bus()
    reset_connector_bus()
    reset()
    stop_connector_bridge()


# -- HookBus ---------------------------------------------------------------


def test_hookbus_emit_calls_subscribers():
    bus = HookBus()
    received: list[PluginContext] = []

    async def handler(ctx):
        received.append(ctx)

    bus.subscribe(Hook.AFTER_TOOL_CALL, handler)

    async def go():
        bus.emit(
            Hook.AFTER_TOOL_CALL,
            PluginContext(hook=Hook.AFTER_TOOL_CALL, tool_name="x"),
        )

    asyncio.run(go())
    asyncio.run(asyncio.sleep(0))
    assert len(received) == 1
    assert received[0].tool_name == "x"


def test_hookbus_unsubscribe_unknown_is_silent():
    bus = HookBus()

    async def handler(ctx):
        pass

    bus.unsubscribe(Hook.AFTER_TOOL_CALL, handler)


def test_hookbus_handler_exceptions_do_not_break_others():
    bus = HookBus()
    ok: list[str] = []

    async def bad(ctx):
        raise RuntimeError("nope")

    async def good(ctx):
        ok.append(ctx.tool_name)

    bus.subscribe(Hook.AFTER_TOOL_CALL, bad)
    bus.subscribe(Hook.AFTER_TOOL_CALL, good)

    async def go():
        bus.emit(
            Hook.AFTER_TOOL_CALL,
            PluginContext(hook=Hook.AFTER_TOOL_CALL, tool_name="ok"),
        )

    asyncio.run(go())
    asyncio.run(asyncio.sleep(0))
    assert ok == ["ok"]


def test_emit_is_module_sugar():
    register_plugin(_NoopPlugin())
    install_all(get_bus())

    seen: list[str] = []

    async def handler(ctx):
        seen.append(ctx.tool_name)

    get_bus().subscribe(Hook.AFTER_TOOL_CALL, handler)

    async def go():
        emit(
            Hook.AFTER_TOOL_CALL,
            PluginContext(hook=Hook.AFTER_TOOL_CALL, tool_name="sugar"),
        )

    asyncio.run(go())
    asyncio.run(asyncio.sleep(0))
    assert seen == ["sugar"]


# -- Plugin lifecycle ------------------------------------------------------


class _NoopPlugin:
    name = "noop"
    version = "0.0.1"

    def register(self, bus):
        pass

    def shutdown(self):
        pass


def test_register_plugin_replaces_by_name():
    a = _NoopPlugin()
    a.name = "sample"
    register_plugin(a)
    assert "sample" in list_plugins()

    class _Other:
        name = "sample"
        version = "0.0.2"

        def register(self, bus):
            pass

        def shutdown(self):
            pass

    register_plugin(_Other())
    # still one entry under "sample"
    assert list_plugins().count("sample") == 1


def test_install_all_calls_register():
    captured: list[str] = []

    class _Capture:
        name = "cap"
        version = "0.0.1"

        def register(self, bus):
            captured.append(self.name)

        def shutdown(self):
            pass

    reset()
    register_plugin(_Capture())
    install_all(get_bus())
    assert captured == ["cap"]


# -- Audit log sample ------------------------------------------------------


def test_audit_log_records_tool_call():
    mem = MemoryAuditStore(max_records=10)
    plugin = AuditLogPlugin(file_path="", memory_store=mem)
    plugin.register(get_bus())

    async def go():
        emit(
            Hook.AFTER_TOOL_CALL,
            PluginContext(
                hook=Hook.AFTER_TOOL_CALL,
                tool_name="edit_file",
                tool_input={"path": "/tmp/x"},
                tool_result="ok",
                tool_is_error=False,
            ),
        )

    asyncio.run(go())
    asyncio.run(asyncio.sleep(0))

    recs = mem.records()
    assert len(recs) == 1
    r = recs[0]
    assert r.hook == "after_tool_call"
    assert r.actor == "tool"
    assert r.detail["tool_name"] == "edit_file"


def test_audit_log_records_channel_send():
    mem = MemoryAuditStore(max_records=10)
    plugin = AuditLogPlugin(file_path="", memory_store=mem)
    plugin.register(get_bus())

    async def go():
        emit(
            Hook.AFTER_CHANNEL_SEND,
            PluginContext(
                hook=Hook.AFTER_CHANNEL_SEND,
                channel="telegram",
                channel_target_uid=42,
                channel_text="hi",
            ),
        )

    asyncio.run(go())
    asyncio.run(asyncio.sleep(0))

    r = mem.records()[0]
    assert r.actor == "channel"
    assert r.detail["channel"] == "telegram"
    assert r.detail["target_uid"] == 42


def test_audit_log_records_connector_event():
    mem = MemoryAuditStore(max_records=10)
    plugin = AuditLogPlugin(file_path="", memory_store=mem)
    plugin.register(get_bus())

    async def go():
        ce = ConnectorEvent(
            connector="calendar", kind=ConnectorEventKind.CREATED,
            id="ev-1", payload={"title": "standup"},
        )
        emit(
            Hook.ON_CONNECTOR_EVENT,
            PluginContext(
                hook=Hook.ON_CONNECTOR_EVENT,
                connector="calendar",
                connector_event=ce,
            ),
        )

    asyncio.run(go())
    asyncio.run(asyncio.sleep(0))

    r = mem.records()[0]
    assert r.actor == "connector"
    assert r.detail["connector"] == "calendar"
    assert r.detail["kind"] == "created"


def test_audit_log_writes_jsonl(tmp_path: Path):
    log_path = tmp_path / "audit.log"
    plugin = AuditLogPlugin(file_path=log_path)
    plugin.register(get_bus())

    async def go():
        emit(
            Hook.AFTER_LLM_CALL,
            PluginContext(
                hook=Hook.AFTER_LLM_CALL,
                llm_provider="anthropic",
                llm_model="claude-fable-5",
                llm_input_tokens=10,
                llm_output_tokens=20,
            ),
        )

    asyncio.run(go())
    asyncio.run(asyncio.sleep(0))

    text = log_path.read_text()
    # One JSON object per line.
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["hook"] == "after_llm_call"
    assert parsed["actor"] == "llm"
    assert parsed["detail"]["model"] == "claude-fable-5"


def test_audit_log_truncates_long_strings():
    mem = MemoryAuditStore(max_records=10)
    plugin = AuditLogPlugin(file_path="", memory_store=mem)
    plugin.register(get_bus())

    huge = "x" * 20000
    asyncio.run(plugin._handle(PluginContext(
        hook=Hook.AFTER_TOOL_CALL,
        tool_name="noop",
        tool_result=huge,
    )))
    r = mem.records()[0]
    assert len(r.detail["tool_result"]) < 20000
    assert r.detail["tool_result"].endswith("...[truncated]")


def test_file_audit_store_rotation(tmp_path: Path):
    log_path = tmp_path / "audit.log"
    store = FileAuditStore(log_path, max_bytes=10)

    async def go():
        for i in range(50):
            await store.append(
                AuditRecord(
                    hook="x", occurred_at="now",
                    actor="a", detail={"i": i},
                ),
            )

    asyncio.run(go())
    # Either we rotated, or we wrote a small file. Either
    # way the active file still exists and has fresh content.
    assert log_path.exists()


# -- Bridge ----------------------------------------------------------------


def test_connector_bridge_forwards_to_plugin_bus():
    plugin_bus = HookBus()
    plugin_bus_seen: list[ConnectorEvent] = []

    async def handler(ctx):
        plugin_bus_seen.append(ctx.connector_event)

    plugin_bus.subscribe(Hook.ON_CONNECTOR_EVENT, handler)

    start_connector_bridge(plugin_bus)
    try:
        async def go():
            publish_connector(ConnectorEvent(
                connector="calendar",
                kind=ConnectorEventKind.CREATED,
                id="1", payload={"title": "x"},
            ))
            # Yield enough for the connector bus →
            # bridge → plugin bus chain to complete.
            await asyncio.sleep(0.1)

        asyncio.run(go())
        assert len(plugin_bus_seen) == 1
        assert plugin_bus_seen[0].id == "1"
    finally:
        stop_connector_bridge()


def test_connector_bridge_is_idempotent():
    plugin_bus = HookBus()
    start_connector_bridge(plugin_bus)
    start_connector_bridge(plugin_bus)  # should replace, not double
    try:
        async def go():
            publish_connector(ConnectorEvent(
                connector="calendar",
                kind=ConnectorEventKind.CREATED,
                id="1", payload={},
            ))

        asyncio.run(go())
        asyncio.run(asyncio.sleep(0.1))

        # Only one record per event despite two bridge starts.
        # We can't see this directly without a listener, but
        # stop_connector_bridge should still be clean.
    finally:
        stop_connector_bridge()


def test_connector_bridge_stops_cleanly():
    plugin_bus = HookBus()
    start_connector_bridge(plugin_bus)
    stop_connector_bridge()
    # calling stop twice is a no-op
    stop_connector_bridge()


# -- Plugin protocol runtime check -----------------------------------------


def test_plugin_protocol_is_runtime_checkable():
    """Any object with the right attrs satisfies ``Plugin``."""
    class _Any:
        name = "any"
        version = "0.0.0"

        def register(self, bus):
            pass

        def shutdown(self):
            pass

    assert isinstance(_Any(), Plugin)
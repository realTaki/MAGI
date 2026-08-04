"""Contract coverage for the BUS-owned durable Tool Catalog."""

from __future__ import annotations

import pytest

from magi.bus import AgentMessage, BusStore, ToolDefinition, bootstrap
from magi.bus.services.tool_catalog import CatalogRevisionConflict, ToolCatalogValidationError
from magi.bus.db import init_orm


@pytest.fixture()
def catalog(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(state))
    init_orm(str(state), seed_root=False)
    return bootstrap(str(state)).tool_catalog


def _definition(name: str, *, source: str = "builtin", roles: tuple[str, ...] = ()) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        source=source,
        description=f"{name} description",
        input_schema={"type": "object", "properties": {"value": {"type": "string"}}},
        allowed_roles=roles,
    )


def test_replace_snapshot_advances_revision_and_disables_removed_tools(catalog) -> None:
    first = catalog.replace_snapshot(source="builtin", definitions=[_definition("alpha"), _definition("beta")])
    second = catalog.replace_snapshot(
        source="builtin",
        expected_previous_revision=first.revision,
        definitions=[_definition("alpha")],
    )

    assert second.revision == first.revision + 1
    assert second.snapshot_hash != first.snapshot_hash
    assert [item.name for item in catalog.list_definitions()] == ["alpha"]
    archived = catalog.get_definition("beta", source="builtin")
    assert archived is not None and not archived.enabled and archived.revision == second.revision


def test_catalog_enforces_role_filter_and_source_name_invariant(catalog) -> None:
    catalog.replace_snapshot(
        source="builtin",
        definitions=[_definition("public"), _definition("operator_only", roles=("operator",))],
    )
    assert [item["name"] for item in catalog.list_schemas(caller_role="guest")] == ["public"]
    assert {item["name"] for item in catalog.list_schemas(caller_role="operator")} == {"public", "operator_only"}

    with pytest.raises(ToolCatalogValidationError, match="already enabled"):
        catalog.replace_snapshot(source="mcp:example", definitions=[_definition("public", source="mcp:example")])


def test_catalog_rejects_stale_writer(catalog) -> None:
    snapshot = catalog.get_snapshot()
    catalog.replace_snapshot(source="builtin", definitions=[_definition("alpha")])
    with pytest.raises(CatalogRevisionConflict):
        catalog.replace_snapshot(
            source="builtin",
            expected_previous_revision=snapshot.revision,
            definitions=[_definition("beta")],
        )


def test_agent_transition_binds_job_to_catalog_snapshot(catalog, tmp_path) -> None:
    published = catalog.replace_snapshot(source="builtin", definitions=[_definition("alpha")])
    store = BusStore(str(tmp_path / "state"))
    run_id = store.publish_agent_message(AgentMessage(event_id="root", text="go", channel="webui"))
    claim = store.claim_next_agent_message("actor")
    assert claim is not None
    store.wait_for_tools(
        claim.event_id,
        continuation={"input": claim.payload, "messages": [], "tool_call_ids": ["call-1"]},
        jobs=[{"tool_call_id": "call-1", "tool_name": "alpha", "arguments": {}, "context": {}}],
    )
    job = store.claim_next_tool_job("tools")
    definition = catalog.get_definition("alpha", source="builtin")
    assert job is not None and definition is not None and job.run_id == run_id
    assert (job.source, job.catalog_revision, job.schema_hash) == (
        "builtin", published.revision, definition.schema_hash,
    )

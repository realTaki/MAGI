"""Worker-owned prompt and BUS-owned skill workspace asset lifecycle."""

from __future__ import annotations

from magi.agent.worker import AgentWorker
from magi.bus.provision import provision_node_storage
from magi.proactive.worker import ProactiveWorker


async def test_workers_seed_only_their_prompt_records_and_skills_seed_at_bus_open(tmp_path) -> None:
    workspace = tmp_path / "eva-000"
    bus = provision_node_storage(state_dir=str(workspace / "memories"), magis_url=None)

    assert bus.prompt_book.list() == []
    assert {item.name for item in bus.skills_book.list()} >= {
        "codebase_search",
        "reminder_template",
        "web_lookup",
    }
    assert (workspace / "skills" / "web_lookup" / "SKILL.md").is_file()

    await AgentWorker(bus).on_start()
    assert bus.prompt_book.soul()
    assert (workspace / "prompts" / "agent" / "soul.md").is_file()
    assert not (workspace / "prompts" / "proactive" / "task_presets" / "defaults.yaml").exists()

    await ProactiveWorker(bus).on_start()
    assert bus.prompt_book.task_presets()
    assert (workspace / "prompts" / "proactive" / "task_presets" / "defaults.yaml").is_file()

    bus.prompt_book.write_workspace_soul("operator persona")
    await AgentWorker(bus).on_start()
    assert bus.prompt_book.soul() == "operator persona"

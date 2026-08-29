"""Worker-owned prompt and BUS-owned skill workspace asset lifecycle."""

from __future__ import annotations

from magi.agent.worker import AgentWorker
from magi.old_bus.firmwares.books.file import PromptBook
from magi.old_bus.provision import provision_node_storage
from magi.proactive.preset_tasks import _load_preset
from magi.proactive.worker import ProactiveWorker


async def test_workers_seed_only_their_prompt_records_and_skills_seed_at_bus_open(tmp_path) -> None:
    workspace = tmp_path / "eva-000"
    bus = provision_node_storage(state_dir=str(workspace / "memories"), magis_url=None)

    assert PromptBook.KNOWN_PROMPTS["agent/soul"] == "Active workspace persona used for every agent turn."
    assert bus.prompt_book.list() == []
    assert {item.name for item in bus.skills_book.list()} >= {
        "codebase_search",
        "reminder_template",
        "web_lookup",
    }
    assert (workspace / "skills" / "web_lookup" / "SKILL.md").is_file()

    await AgentWorker(bus).on_start()
    assert bus.prompt_book.get(key="agent/soul")
    assert (workspace / "prompts" / "agent" / "soul.md").is_file()
    assert not (workspace / "prompts" / "proactive" / "task_presets").exists()

    await ProactiveWorker(bus).on_start()
    preset_keys = {
        key.removeprefix("proactive/")
        for key in bus.prompt_book.list()
        if key.startswith("proactive/")
    }
    assert preset_keys == {
        "daily_standup_brief",
        "weekly_review",
        "morning_brief",
        "night_summary",
    }
    assert (workspace / "prompts" / "proactive" / "daily_standup_brief.md").is_file()
    assert _load_preset(bus, "daily_standup_brief")["prompt"].startswith("You are generating")

    bus.prompt_book.set(key="agent/soul", value="operator persona")
    bus.prompt_book.register(key="agent/soul", value="upgraded module default")
    assert bus.prompt_book.get(key="agent/soul") == "operator persona"
    bus.prompt_book.reset(key="agent/soul")
    assert bus.prompt_book.get(key="agent/soul") == "upgraded module default"
    assert bus.prompt_book.delete(key="agent/soul")
    assert bus.prompt_book.get(key="agent/soul") != "operator persona"

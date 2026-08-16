"""Agent-owned prompt defaults and idempotent BUS registration."""

from __future__ import annotations

from importlib.resources import files


_TEXT_DEFAULTS: tuple[tuple[str, str], ...] = (
    ("agent/defaults/soul", "soul.md"),
    ("agent/soul", "soul.md"),
    ("agent/defaults/fallback_persona", "fallback_persona.md"),
    ("agent/chat_titles", "chat_titles.md"),
    ("agent/compaction", "compaction.md"),
    ("agent/context/skills_block", "skills_block.md"),
)


def ensure_agent_prompt_defaults(prompt_book) -> None:
    """Seed AgentWorker-owned prompts into the workspace PromptBook.

    Existing records are intentionally never overwritten: operators edit the
    workspace copy through BUS, while package assets only supply first-run
    defaults.
    """
    assets = files("magi.agent.assets")
    for key, filename in _TEXT_DEFAULTS:
        prompt_book.ensure(key, assets.joinpath(filename).read_text(encoding="utf-8"), suffix=".md")


__all__ = ["ensure_agent_prompt_defaults"]

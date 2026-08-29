"""Agent-owned prompt defaults and idempotent BUS registration."""

from __future__ import annotations

from importlib.resources import files

_TEXT_PROMPTS: tuple[tuple[str, str], ...] = (
    ("agent/soul", "soul.md"),
    ("agent/chat_titles", "chat_titles.md"),
    ("agent/compaction", "compaction.md"),
    ("agent/skills_block", "skills_block.md"),
)


def ensure_agent_prompt_defaults(prompt_book) -> None:
    """Seed AgentWorker-owned prompts into the workspace PromptBook.

    Default/active lifecycle is implemented by
    :meth:`PromptBook.register`; this Worker only supplies the
    owner keys and package content.
    """
    prompts = files("magi.agent.prompts")
    for active_key, filename in _TEXT_PROMPTS:
        content = prompts.joinpath(filename).read_text(encoding="utf-8")
        prompt_book.register(key=active_key, value=content)


__all__ = ["ensure_agent_prompt_defaults"]

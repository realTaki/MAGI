"""Agent-owned prompt defaults and idempotent BUS registration."""

from __future__ import annotations

from importlib.resources import files

_TEXT_PROMPTS: tuple[tuple[str, str], ...] = (
    ("agent/soul", "soul.md"),
    ("agent/compaction", "compaction.md"),
    ("agent/skills_block", "skills_block.md"),
)


def prompt_defaults() -> tuple[tuple[str, str], ...]:
    """Return Agent-owned defaults for publication through ``RegisterPromptJob``."""
    prompts = files("agent.prompts")
    return tuple(
        (active_key, prompts.joinpath(filename).read_text(encoding="utf-8"))
        for active_key, filename in _TEXT_PROMPTS
    )


__all__ = ["prompt_defaults"]

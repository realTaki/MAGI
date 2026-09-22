"""ProactiveWorker-owned Markdown defaults and BUS registration."""

from __future__ import annotations

from importlib.resources import files

_PROMPT_DEFAULTS: tuple[tuple[str, str], ...] = (
    ("proactive/daily_standup_brief", "daily_standup_brief.md"),
    ("proactive/weekly_review", "weekly_review.md"),
    ("proactive/morning_brief", "morning_brief.md"),
    ("proactive/night_summary", "night_summary.md"),
)


def ensure_proactive_prompt_defaults(prompt_book) -> None:
    """Seed one managed Markdown file for each proactive task preset."""
    prompts = files("proactive.prompts")
    for key, filename in _PROMPT_DEFAULTS:
        prompt_book.register(
            key=key,
            value=prompts.joinpath(filename).read_text(encoding="utf-8"),
        )


__all__ = ["ensure_proactive_prompt_defaults"]

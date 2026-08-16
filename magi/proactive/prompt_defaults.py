"""ProactiveWorker-owned prompt defaults and idempotent BUS registration."""

from __future__ import annotations

from importlib.resources import files

import yaml


def ensure_proactive_prompt_defaults(prompt_book) -> None:
    """Seed proactive task presets into the workspace PromptBook."""
    content = files("magi.proactive.assets").joinpath("defaults.yaml").read_text(encoding="utf-8")
    payload = yaml.safe_load(content)
    if not isinstance(payload, dict):
        raise ValueError("proactive defaults.yaml must contain a mapping")
    prompt_book.ensure("proactive/task_presets/defaults", payload, suffix=".yaml")


__all__ = ["ensure_proactive_prompt_defaults"]

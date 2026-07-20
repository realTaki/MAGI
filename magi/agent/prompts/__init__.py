"""Prompt templates shipped with the runtime.

Every piece of natural-language text the runtime emits to a
human (or to the LLM) lives under this directory. The
codebase never embeds such text as a string literal — when
an operator wants to change wording, the search starts here,
not in a Python file.

Files:

  - ``soul.md``            : the per-deployer persona the agent
                             loop passes as the system prompt.
                             Read by
                             :mod:`magi.agent.workspace` /
                             :mod:`magi.agent.loop`.
  - ``fallback_persona.md`` : last-resort persona used when
                             both the workspace ``SOUL.md`` and
                             the bundled ``soul.md`` are
                             missing (broken install / wiped
                             volume). The text is intentionally
                             generic so it can't accidentally
                             leak the bundled soul to a
                             misconfigured deployer.
  - ``bot_replies.md``     : Telegram bot reply templates.
                             YAML key→text; values use Python
                             ``str.format()`` placeholders.
  - ``chat_titles.md``     : the system prompt for the
                             background "summarize a conversation
                             into a 3-5 word title" job. Read by
                             :mod:`magi.agent.memory.session.auto_title`.

Loading is **eager at first use** and cached in module-level
globals so subsequent calls are O(1). The cache survives
across requests; if an operator edits a file in place they
need to restart the process for the change to take effect
(C4 will move to a file-watcher + reload model).
"""

from __future__ import annotations

import logging
from pathlib import Path
from threading import Lock
from typing import Final

import yaml

logger = logging.getLogger("magi.agent.prompts")

# Directory this __init__ lives in. Prompts are co-located
# next to the loader so a single ``git mv`` moves the
# whole subsystem.
_PROMPTS_DIR: Final[Path] = Path(__file__).resolve().parent

# Cache: path → (text, error). Filled lazily on first read.
_cache: dict[str, str] = {}
_cache_lock = Lock()


def _load(name: str) -> str:
    """Read a prompt file by short name (e.g. ``"soul"``).

    Plain-text files (``.md``) are returned stripped;
    YAML files (``.yaml`` / ``.yml``) are returned as a
    ``str`` that is a YAML dump of the parsed mapping. The
    ``bot_replies`` loader is a thin wrapper that asks for
    the YAML form and ``yaml.safe_load`` it.
    """
    if name in _cache:
        return _cache[name]
    with _cache_lock:
        if name in _cache:
            return _cache[name]
        # ``.md`` first, fall back to ``.yaml`` / ``.yml``.
        for suffix in (".md", ".yaml", ".yml"):
            path = _PROMPTS_DIR / f"{name}{suffix}"
            if path.is_file():
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError as exc:
                    logger.exception("failed to read prompt %s", path)
                    raise FileNotFoundError(
                        f"prompt {name!r} could not be read: {exc}"
                    ) from exc
                _cache[name] = text.strip() if suffix == ".md" else text
                return _cache[name]
        # Not found at any extension.
        raise FileNotFoundError(
            f"prompt {name!r} not found in {_PROMPTS_DIR} "
            "(looked for .md, .yaml, .yml)"
        )


# -- public loaders ----------------------------------------------------------

def load_soul() -> str:
    """Return the bundled ``soul.md`` (the deployer's persona)."""
    return _load("soul")


def load_fallback_persona() -> str:
    """Return the bundled ``fallback_persona.md``.

    Used by :mod:`magi.agent.loop` only when the
    workspace's ``SOUL.md`` and the bundled ``soul.md`` are
    both missing. Kept as its own file so the operator can
    tune the *fallback* wording without touching the
    persona they actually deploy.
    """
    return _load("fallback_persona")


def load_chat_title_prompt() -> str:
    """The system prompt for the auto-title worker (D.7).

    Reads the bundled ``chat_titles.md``. Used by
    :mod:`magi.agent.memory.session.auto_title` to summarise each
    session's first user message into a 3-5 word title
    written back to ``Session.title``.
    """
    return _load("chat_titles")


def load_compaction_prompt() -> str:
    """The system prompt for the auto-compact worker (D.17)."""
    return _load("compaction")


def load_memory_block_template() -> str:
    """The "Long-term memory (MAGI)" block the agent loop
    appends to the system prompt.

    Reads the bundled ``memory_block.md``. The block is
    the static header + intro + the two ``### 重要的事``
    / ``### 正在进行`` sub-section headings; the rows
    themselves are appended by :func:`format_memory_block`
    in :mod:`magi.agent.memory.magi.prompt` (which then
    string-splits the template at the ``### 重要的事``
    marker to drop the empty placeholders when no rows
    land under a kind).
    """
    return _load("memory_block")


def load_contact_block_template() -> str:
    """The "Current chatter" block the agent loop appends
    to the system prompt when a contact row exists for the
    current chat's chatter. See
    :func:`magi.agent.memory.contacts.prompt.format_contact_block`
    for how the template is combined with the contact row.
    """
    return _load("contact_block")


def load_skills_block_template() -> str:
    """The "Available skills" block the agent loop appends
    to the system prompt when any SKILL.md is registered.
    See :func:`magi.agent.tools.skill_loader.format_skills_block`
    for the per-skill bullet rendering that follows.
    """
    return _load("skills_block")


def load_bot_replies() -> dict[str, str]:
    """Return the Telegram bot reply templates as
    ``{template_id: text}``.

    Values use ``str.format()`` placeholders — the loader
    does not interpolate; callers do, e.g.::

        replies = load_bot_replies()
        await update.effective_message.reply_text(
            replies["cross_company_refusal"].format(
                emp_name=emp.name, chat_id=chat_id,
            ),
        )

    Raises ``KeyError`` if a caller asks for a missing id;
    the bot should treat that as a programming error (not
    a runtime fallback) so a missing template surfaces in
    smoke tests instead of silently dropping the reply.
    """
    raw = _load("bot_replies")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError(
            f"bot_replies.yaml must be a mapping; got {type(data).__name__}"
        )
    # Defensive cast: every value is a string template.
    out: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(value, str):
            raise ValueError(
                f"bot_replies.yaml key {key!r} is not a string template"
            )
        out[key] = value
    return out


def reset_cache() -> None:
    """Drop the in-memory cache. Test-only — production code
    never calls this. The runtime loads each file at most
    once per process."""
    with _cache_lock:
        _cache.clear()

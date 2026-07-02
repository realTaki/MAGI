"""The agent loop — the spine every channel plugs into.

v0 (LLM minimum-viable): one chat turn. Read SOUL.md for
persona, build a one-message history, call the LLM, return
the reply. No skills, no memory, no proactive triggers —
those land in C4/C5. The audit row for each turn (inbound +
outbound, with thinking block captured) is the contract that
later checkpoints build on.

Why this is a function and not a class: the agent loop
doesn't have per-instance state in v0. Channels call
``handle_message`` with everything the call needs
(credentials + text) and get a string back. C4 will move
per-conversation state (history, scratchpad) onto a class so
multi-turn calls can pass it in; the function signature will
gain a ``conversation_id`` arg without breaking callers.

Audit hooks: ``handle_message`` writes two rows per call —
inbound (the user's message) and outbound (the LLM reply).
For v0 the rows go to the ``meta`` table via a tiny
``audit_log`` JSON blob keyed on timestamp; the proper
SQLAlchemy ``AuditEvent`` model lands with C1.1's ORM pass
and replaces this without changing the call sites.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from magi.runtime.llm import ChatMessage, LLMError, get_provider
from magi.runtime.state.settings import state_get, state_set

logger = logging.getLogger("magi.runtime.agent")

# Default cap on a single LLM reply. 1024 is enough for chat
# turns and well under the 8K most Anthropic-compatible APIs
# advertise. Callers can override per-call.
DEFAULT_MAX_TOKENS = 1024

# Friendly fallback the user sees when the LLM call fails
# for any reason. The real error is logged + audited; the
# user gets a stable string so the conversation doesn't look
# like a crash.
FALLBACK_REPLY = "我现在有点忙，等会儿再试一次吧。"

# Where SOUL.md lives. C4 will move this to a per-employee
# path (each EVE can have its own persona); for v0 we read
# the single workspace file at startup.
_SOUL_FILENAME = "SOUL.md"

# Hardcoded system-prompt fallback when SOUL.md is missing.
# The deployer is expected to ship one (the workspace
# bootstrap creates a default), but the runtime must not
# crash if it's been deleted.
_DEFAULT_PERSONA = "You are a helpful enterprise assistant."


def _read_soul(state_dir: str) -> str:
    """Load the persona text from ``<state_dir>/SOUL.md``.

    The file lives next to the SQLite DB (one level up from
    the ``memories/`` subdir in the typical layout). For the
    smoke test, the workspace bootstrap creates a starter
    SOUL.md on first boot.
    """
    soul_path = Path(state_dir).parent / _SOUL_FILENAME
    if not soul_path.exists():
        # Fall back to the same dir as the DB; the bootstrap
        # may have written it there depending on the layout
        # the deployer chose.
        soul_path = Path(state_dir) / _SOUL_FILENAME
    if not soul_path.exists():
        return _DEFAULT_PERSONA
    text = soul_path.read_text(encoding="utf-8").strip()
    return text or _DEFAULT_PERSONA


def _resolve_system_default(
    state_dir: str,
) -> tuple[str, str, str | None]:
    """Read the system LLM default from the meta table.

    Returns (provider, api_key, model_or_none). Raises
    ``LLMError`` if no system default is configured.
    """
    provider = state_get(state_dir, "llm.default_provider")
    api_key = state_get(state_dir, "llm.default_api_key")
    model = state_get(state_dir, "llm.default_model") or None
    if not provider or not api_key:
        raise LLMError(
            "No LLM configured. Set llm.default_provider + "
            "llm.default_api_key in the meta table, or assign "
            "a provider + api_key to the employee."
        )
    return provider, api_key, model


def _write_audit(
    state_dir: str,
    *,
    kind: str,
    employee_id: int | None,
    channel: str,
    payload: dict[str, Any],
) -> None:
    """Append one event to the ``audit_log`` meta key.

    Pre-C1.1 the audit table isn't a real SQLAlchemy model;
    we stash rows as a JSON list under one meta key. The
    proper hash-chained ``AuditEvent`` model lands with
    C1.1 and replaces this without changing the agent
    call signature. The temporary shape is::

        audit_log: [
          {"ts": "...", "kind": "chat.inbound", "channel": "tg",
           "employee_id": 1, "payload": {...}},
          ...
        ]
    """
    log_raw = state_get(state_dir, "audit_log") or "[]"
    try:
        rows = json.loads(log_raw)
    except (ValueError, TypeError):
        logger.warning("audit_log meta is not valid JSON; resetting")
        rows = []
    if not isinstance(rows, list):
        rows = []

    rows.append(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "channel": channel,
            "employee_id": employee_id,
            "payload": payload,
        }
    )
    # Cap at 1000 rows for v0; C1.1's proper audit table
    # handles retention properly. Old rows just fall off.
    if len(rows) > 1000:
        rows = rows[-1000:]
    state_set(state_dir, "audit_log", json.dumps(rows, ensure_ascii=False))


async def handle_message(
    state_dir: str,
    *,
    text: str,
    channel: str,
    employee_id: int | None = None,
    # Employee-level credentials take precedence over the
    # system default. Both come from the caller (the TG
    # channel or the WebUI chat API) — the agent doesn't
    # touch the DB, which keeps it free of ORM coupling.
    employee_provider: str | None = None,
    employee_api_key: str | None = None,
    employee_model: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    """One chat turn. Returns the LLM's reply text.

    On any LLM failure, returns :data:`FALLBACK_REPLY` and
    audits the real error. The caller (TG bot / WebUI chat)
    is responsible for delivering the string; we don't raise
    into the transport layer because the user already pressed
    send, and a transport-level exception would just confuse
    the UI.

    Parameters
    ----------
    state_dir
        The on-disk state directory (``MAGI_STATE_DIR``).
    text
        The inbound message text.
    channel
        Tag for the audit row (``"tg"`` / ``"webui"`` /
        ``"scheduled"``). Free-form string; no enum yet.
    employee_id
        Optional employee id, for the audit row. ``None`` for
        anonymous (WebUI) traffic.
    employee_provider / employee_api_key / employee_model
        Per-call LLM credentials. If ``employee_provider`` is
        set, ``employee_api_key`` must also be set (or the
        call falls back to the system default). The TG
        channel fetches the key from the employee row before
        calling here; the WebUI chat API passes the
        ``magi_session`` chat_id's admin defaults.
    """
    # Inbound audit. Written before the LLM call so a crash
    # in the provider still leaves a record of the message.
    _write_audit(
        state_dir,
        kind="chat.inbound",
        employee_id=employee_id,
        channel=channel,
        payload={"text": text},
    )

    # Resolve credentials: per-employee first, then system
    # default. Empty strings are treated as "not set" so a
    # half-cleared row doesn't accidentally route to a
    # broken provider.
    if employee_provider and employee_api_key:
        provider_name = employee_provider
        api_key = employee_api_key
        model = employee_model
    else:
        try:
            provider_name, api_key, model = _resolve_system_default(state_dir)
        except LLMError as e:
            logger.warning(
                "no LLM configured; returning fallback (employee=%s): %s",
                employee_id, e,
            )
            _write_audit(
                state_dir,
                kind="chat.outbound.error",
                employee_id=employee_id,
                channel=channel,
                payload={"error": str(e), "text": FALLBACK_REPLY},
            )
            return FALLBACK_REPLY

    soul = _read_soul(state_dir)

    try:
        provider = get_provider(provider_name, api_key, model)
        result = await provider.chat(
            system=soul,
            messages=[ChatMessage(role="user", content=text)],
            max_tokens=max_tokens,
        )
    except LLMError as e:
        logger.warning(
            "llm call failed (employee=%s provider=%s): %s",
            employee_id, provider_name, e,
        )
        _write_audit(
            state_dir,
            kind="chat.outbound.error",
            employee_id=employee_id,
            channel=channel,
            payload={
                "error": str(e),
                "error_class": type(e).__name__,
                "provider": provider_name,
                "model": model,
                "text": FALLBACK_REPLY,
            },
        )
        return FALLBACK_REPLY

    # Outbound audit. ``text`` is the user-facing reply;
    # ``thinking`` is captured separately for debugging
    # (never sent to the user). The full raw_blocks list
    # is included so a future replay tool can reconstruct
    # the exact upstream response.
    _write_audit(
        state_dir,
        kind="chat.outbound",
        employee_id=employee_id,
        channel=channel,
        payload={
            "text": result.text,
            "thinking": result.thinking,
            "model": result.model,
            "provider": provider.name,
            "usage": result.usage,
            "raw_blocks": result.raw_blocks,
        },
    )
    logger.info(
        "llm reply",
        extra={
            "employee_id": employee_id,
            "channel": channel,
            "provider": provider.name,
            "model": result.model,
            "text_len": len(result.text),
            "thinking_len": len(result.thinking) if result.thinking else 0,
        },
    )
    return result.text

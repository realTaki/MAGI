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

# Two friendly strings the agent loop can return when
# something is wrong: ``agent_fallback`` for an LLM call
# that failed mid-stream (network / rate-limit / context-
# length), and ``agent_no_credentials`` for the strict-mode
# rejection when the chat caller never supplied per-
# employee credentials. Both live in
# ``magi/runtime/prompts/bot_replies.yaml`` — see that file
# to tweak. Resolved lazily and cached so a single YAML
# read serves every fallback for the rest of the process.
from magi.runtime.prompts import load_bot_replies  # noqa: E402

_FALLBACK_REPLY_CACHE: dict[str, str] = {}


def _fallback_reply(key: str = "agent_fallback") -> str:
    """Resolve a friendly fallback string from the
    bot_replies prompt table. Cached so a single YAML read
    serves every fallback for the rest of the process.

    ``key`` selects which template: ``agent_fallback`` for
    LLM-call failures (the legacy single-purpose template),
    ``agent_no_credentials`` for the strict-mode rejection
    that tells the user where to fix the missing config.
    """
    cached = _FALLBACK_REPLY_CACHE.get(key)
    if cached is None:
        cached = load_bot_replies()[key]
        _FALLBACK_REPLY_CACHE[key] = cached
    return cached

# Where SOUL.md lives. C4 will move this to a per-employee
# path (each EVE can have its own persona); for v0 we read
# the single workspace file at startup.
_SOUL_FILENAME = "SOUL.md"

def _read_soul(state_dir: str) -> str:
    """Load the persona text from the workspace's ``SOUL.md``.

    Path resolution goes through :func:`magi.runtime.workspace.workspace_root`
    so a deployer that sets ``MAGI_WORKSPACE_DIR`` (state lives
    outside the workspace tree) still gets the right file.

    This is a **read** function — it does not bootstrap or write
    to disk. :func:`magi.runtime.workspace.bootstrap_workspace`
    runs once at boot from ``magi.node`` and is responsible
    for keeping ``SOUL.md`` in place. If the file is still
    missing (e.g. operator wiped the workspace mid-run, or the
    bundled prompt is absent from the install), we fall back to
    the bundled ``prompts/fallback_persona.md`` rather than
    write anything — the agent loop should never silently
    mutate on-disk state.
    """
    from magi.runtime.prompts import load_fallback_persona
    from magi.runtime.workspace import workspace_root

    soul_path = workspace_root(state_dir) / _SOUL_FILENAME
    try:
        text = soul_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return load_fallback_persona()
    text = text.strip()
    return text or load_fallback_persona()


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


def _record_token_usage(
    state_dir: str,
    *,
    employee_id: int,
    channel: str,
    provider: str,
    model: str | None,
    usage: dict,
) -> None:
    """Insert one ``token_usage`` row for a successful LLM call.

    Synchronous because we're already past the async boundary
    (the LLM returned). The SQL insert is one row in a
    dedicated table; latency is bounded by SQLite WAL commit
    (~ms). Pushing it onto the asyncio event loop would add
    bookkeeping for no measurable gain.

    ``usage`` keys follow the Anthropic SDK's ``Usage`` shape
    (see :class:`magi.runtime.llm.provider.ChatResult.usage`).
    Unknown keys are ignored; missing keys default to 0 so
    a provider that returned no usage metadata still gets a
    row (call count stays honest).

    Raises whatever the ORM raises — caller is responsible
    for swallowing (we don't want a transient DB hiccup to
    break a chat that already succeeded).
    """
    from magi.runtime.state.orm import TokenUsage, open_session

    in_t = int(usage.get("input_tokens") or 0)
    out_t = int(usage.get("output_tokens") or 0)
    cc_t = int(usage.get("cache_creation_input_tokens") or 0)
    cr_t = int(usage.get("cache_read_input_tokens") or 0)

    with open_session() as session:
        session.add(TokenUsage(
            employee_id=employee_id,
            channel=channel,
            provider=provider,
            model=model,
            input_tokens=in_t,
            output_tokens=out_t,
            cache_creation_tokens=cc_t,
            cache_read_tokens=cr_t,
        ))
        session.commit()


async def handle_message(
    state_dir: str,
    *,
    text: str,
    channel: str,
    employee_id: int | None = None,
    # D.6: optional session id. Pure audit annotation for
    # v0 — the agent loop itself does not (yet) read session
    # history. The id is echoed into the audit_log rows so
    # future replay tools can join messages back to the
    # persisted session file under
    # ``<workspace>/memories/sessions/<chat_id>/<id>.json``.
    session_id: str | None = None,
    # Per-employee credentials — the chat path is strict by
    # default (no fall-back to a system default) so every LLM
    # call can be billed to a specific employee. Both must be
    # set together or the call is rejected with the
    # ``agent_fallback`` friendly reply.
    employee_provider: str | None = None,
    employee_api_key: str | None = None,
    employee_model: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    """One chat turn. Returns the LLM's reply text.

    On any LLM failure (including missing per-employee
    credentials), returns the ``agent_fallback`` template
    (see ``magi/runtime/prompts/bot_replies.yaml``) and
    audits the real error. The caller (TG bot / WebUI chat)
    is responsible for delivering the string; we don't raise
    into the transport layer because the user already pressed
    send, and a transport-level exception would just confuse
    the UI.

    No default-LLM fallback. Every LLM call must carry the
    employee credentials that pay for it — the design is
    "every message is billed to a person", so silent fall-back
    to a house-LLM (which would mis-route the reply and hide
    the configuration mistake) is deliberately not supported.
    The pre-flight rejection is loud enough that the user
    can fix it from the dashboard / config panel.

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
    session_id
        D.6: optional chat session id. Pure audit-only field
        in v0 — the agent loop itself does not read session
        history. Echoed into all ``chat.*`` audit rows so
        future replay tools can join audit_log entries back
        to the persisted session file under
        ``<workspace>/memories/sessions/<chat_id>/<id>.json``.
    employee_provider / employee_api_key / employee_model
        Per-call LLM credentials. Either all three are set
        (employee chooses model optionally) or the call is
        rejected. The TG channel fetches these from the
        employee row; the WebUI chat API does the same via
        the ``magi_session`` admin cookie.
    """
    # Inbound audit. Written before the LLM call so a crash
    # in the provider still leaves a record of the message.
    _write_audit(
        state_dir,
        kind="chat.inbound",
        employee_id=employee_id,
        channel=channel,
        payload={"text": text, "session_id": session_id},
    )

    # Strict-mode pre-flight: per-employee credentials must
    # be present in full. We treat empty strings as "not set"
    # so a half-cleared row doesn't accidentally route to
    # a broken provider. The user-friendly reply points the
    # user at the panel that fixes the config (TG users will
    # see this; WebUI users hit a 403 one layer up before
    # getting here).
    if not employee_provider or not employee_api_key:
        reason = (
            "no per-employee credentials configured"
            if employee_provider is None and employee_api_key is None
            else "per-employee credentials partially configured "
                 "(provider or key missing)"
        )
        logger.warning(
            "chat rejected (employee=%s channel=%s): %s",
            employee_id, channel, reason,
        )
        _write_audit(
            state_dir,
            kind="chat.outbound.error",
            employee_id=employee_id,
            channel=channel,
            payload={
                "error": reason,
                "session_id": session_id,
                "text": _fallback_reply("agent_no_credentials"),
            },
        )
        return _fallback_reply("agent_no_credentials")

    provider_name = employee_provider
    api_key = employee_api_key
    model = employee_model

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
                "session_id": session_id,
                "text": _fallback_reply(),
            },
        )
        return _fallback_reply()

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
            "session_id": session_id,
            "raw_blocks": result.raw_blocks,
        },
    )

    # D.15 — per-employee token accounting. Written after
    # the audit row so the audit remains the authoritative
    # "what happened" record. Failure here is logged but
    # does NOT raise: a missing token_usage row is a
    # statistical gap, not a user-visible failure mode.
    #
    # Every chat call in v0 has a concrete ``employee_id`` —
    # both channel paths (WebUI cookie admin + TG bound
    # employee) resolve to a row before reaching the LLM.
    # If a future channel ever arrives without a
    # ``chat_id`` → ``Employee`` mapping, the FK NOT NULL
    # here will surface that gap at write time instead of
    # silently dropping the row.
    try:
        _record_token_usage(
            state_dir,
            employee_id=employee_id,
            channel=channel,
            provider=provider.name,
            model=result.model,
            usage=result.usage or {},
        )
    except Exception:
            logger.exception(
                "agent: token_usage insert failed (employee=%s, "
                "channel=%s); chat reply already succeeded",
                employee_id, channel,
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

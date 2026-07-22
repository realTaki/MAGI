"""``send_message`` tool — deliver a side-channel message
to the user without leaving the tool loop.

Use case: the LLM is partway through a multi-turn tool
chain (e.g. "read SOUL, list skills, then reply") and
wants to give the user a status update ("Reading your
SOUL...") instead of going silent for the full tool
chain duration.

IM-target resolution
---------------------

The push target comes from the **session row**
(``chat_sessions.tgid``), not ``ctx.chat_id``. The
session is the single source of truth for "which IM
endpoint does this conversation push to" — populated
at session-creation time by the channel adapter
(WebUI / TG / Task) and never re-derived mid-tool.

  - WebUI session → ``tgid`` is the operator's
    ``employee.telegram_id`` (1-on-1 web chat with the
    bot). The tool v0 returns ``is_error=True`` because
    the WebUI operator already sees the LLM's final
    reply inline; an extra "tool message" would just
    duplicate the chat scroll.
  - TG session → ``tgid`` is the TG chat's ID (private
    or group). The tool pushes via the bot reference
    the agent loop injects via ``_tg_send_callback``.
  - Task session (``channel="task"``) → ``tgid`` is
    the task's delivery target (TG chat_id for TG
    tasks, the operator's telegram_id for webui tasks
    — the latter as a breadcrumb since the runner's
    "fresh session per fire" semantics means there's
    no live chat to push to).

The handler is injected via ``ToolContext`` because the
agent loop owns the TG bot reference (not the tool).
"""

from __future__ import annotations

from typing import Any, Callable, Awaitable

from magi.agent.tools.base import Tool, ToolContext, ToolResult
from magi.agent.memory.session.tables import (
    ChatMessage as _DbChatMessage,  # noqa: F401
    ChatSession as _DbChatSession,
)

# Type alias for the optional TG callback. The agent
# loop injects one when the session's IM target is TG
# and a bot is registered; for sessions whose target
# isn't TG (or no bot is live), it's ``None``.
TGCallback = Callable[[int, str], Awaitable[None]] | None

_MAX_TEXT_LEN = 4000  # matches the TG API limit (4096 with buffer)


def _resolve_tg_target(session_id: str) -> tuple[str, int | None]:
    """Look up the IM target for ``session_id``.

    Returns ``(source, tgid_int)`` where ``source`` is a
    debug-friendly tag (``"session:<id>"``) and ``tgid_int``
    is the parsed TG chat_id for TG-targeted sessions,
    or ``None`` for sessions that don't have a TG target
    (webui chat-history-only conversations).

    The session row carries the IM choice; we don't
    derive it from ``ctx`` because a single agent
    invocation may serve multiple sessions over its
    lifetime and the tool needs an authoritative
    address per call.
    """
    from magi.agent.db import open_session
    if not session_id:
        return ("(no session)", None)
    try:
        with open_session() as db:
            sess = db.get(_DbChatSession, session_id)
        if sess is None:
            return (f"(missing session:{session_id})", None)
        if not sess.tgid:
            return (f"(session {session_id} has no tgid)", None)
        try:
            return (f"session:{session_id}", int(sess.tgid))
        except (TypeError, ValueError):
            return (f"session:{session_id}(non-numeric tgid)", None)
    except Exception:
        # DB hiccup must not block the tool — fall back
        # to a disabled state.
        return (f"(session lookup failed for {session_id})", None)


class SendMessageTool(Tool):
    """Send a side-channel message to the current user."""

    name = "send_message"

    # Visible only to ``admin`` and ``assigned``
    # operators — same gate as the WebUI dashboard and
    # as ``ScheduleTaskTool`` / the action-item trio.
    # The chat path always passes the operator's role
    # through to ``handle_message(caller_role=...)`` so
    # non-eligible callers never see these tools in the
    # LLM's menu. ``MCPTool`` is intentionally permissive
    # (operator-configured at the MCP server level).
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Deliver a message to the current user without "
        "ending the tool loop. Use sparingly — most "
        "communication should happen in the final reply. "
        "On Telegram this is delivered as a normal message; "
        "on WebUI v0 the tool is disabled (returns an "
        "error) because the operator already sees the "
        "final reply inline."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": (
                    "Message body. Up to 4000 characters."
                ),
            },
        },
        "required": ["text"],
    }

    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        text = kwargs.get("text")
        if not isinstance(text, str) or not text:
            return ToolResult(
                content="send_message: ``text`` is required and must be a non-empty string",
                is_error=True,
            )
        if len(text) > _MAX_TEXT_LEN:
            return ToolResult(
                content=(
                    f"send_message: text is {len(text)} chars; "
                    f"v0 limit is {_MAX_TEXT_LEN}."
                ),
                is_error=True,
            )

        # Resolve the TG target from the session row.
        # Sessions are the single source of truth for the
        # IM endpoint — there's no separate ``chat_id``
        # concept. The agent loop never talks to "another
        # person" — it's always talking to MAGI itself, so
        # the session's ``tgid`` is the only address the
        # tool needs.
        source, target_id = _resolve_tg_target(ctx.session_id)
        if target_id is None:
            # No IM target resolved: either no session
            # row exists (caller misuse) or the session
            # has no TG target (a webui conversation —
            # the operator already sees the LLM's final
            # text reply inline). Tool-level error so the
            # LLM can react ("no-op push; reply text lands
            # in chat history").
            return ToolResult(
                content=(
                    f"send_message: no TG target on session "
                    f"{ctx.session_id!r}; the reply will "
                    "land in chat history instead."
                ),
                is_error=True,
            )

        # Call the callback the agent loop injected.
        callback = kwargs.get("_tg_send_callback")
        if not callable(callback):
            return ToolResult(
                content=(
                    "send_message: TG callback not wired into "
                    "the tool context; this is a programming "
                    "error, not a runtime condition."
                ),
                is_error=True,
            )

        try:
            await callback(target_id, text)
        except Exception as e:
            return ToolResult(
                content=f"send_message: TG send failed: {e}",
                is_error=True,
            )

        return ToolResult(
            content=(
                f"send_message: delivered {len(text)} chars "
                f"to chat {target_id} ({source})"
            )
        )
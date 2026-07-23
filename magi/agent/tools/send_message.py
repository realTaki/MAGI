"""``send_message`` tool — deliver a side-channel message
to the user without leaving the tool loop.

Use case: the LLM is partway through a multi-turn tool
chain (e.g. "read SOUL, list skills, then reply") and
wants to give the user a status update ("Reading your
SOUL...") instead of going silent for the full tool
chain duration.

IM-target resolution (D.28)
----------------------------

The push target is determined by the **session's channel**
(``chat_sessions.channel``) and the dispatcher's adapter for
that channel. The tool calls ``dispatcher.send_to_session(
session_id, text)`` — it never reads the per-channel IM id
itself (no tgid, no slack mid, etc.); that's the adapter's
job.

  - WebUI session (``channel="webui"``) — the tool
    returns ``is_error=True`` because the WebUI operator
    already sees the LLM's final reply inline. There's no
    webui adapter today (we push to the chat scroll, not
    out-of-band).
  - TG session (``channel="tg"``) — the TG adapter
    resolves the user's bound chat id and pushes via the
    python-telegram-bot client.
  - Task session (``channel="task"``) — the task
    runner's adapter pushes the reply via the same
    channel that originated the task.

The tool stays channel-agnostic. Adding Slack later = write
a Slack adapter + register it; this tool doesn't change.
"""

from __future__ import annotations

from typing import Any

from magi.agent.tools.base import Tool, ToolContext, ToolResult


_MAX_TEXT_LEN = 4000  # matches common IM client caps (TG 4096, Slack 40k, ...)


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
        "The dispatcher routes the push to whatever "
        "channel the session belongs to (Telegram, "
        "WebUI scroll, etc.); the tool itself is channel-"
        "agnostic."
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

        # Empty session_id means the tool is being called
        # outside a session context (rare — agent-loop test
        # harnesses, edge cases). Surface as a clear error.
        if not ctx.session_id:
            return ToolResult(
                content=(
                    "send_message: no session context; "
                    "the LLM must be invoked from inside a "
                    "session for side-channel push."
                ),
                is_error=True,
            )

        # Hand off to the dispatcher. The dispatcher reads
        # ``chat_sessions.channel`` for ``session_id``, picks
        # the right adapter, and the adapter handles its own
        # IM id resolution + transport. This tool stays
        # channel-agnostic.
        from magi.channels import dispatcher

        try:
            await dispatcher.send_to_session(ctx.session_id, text)
        except KeyError as e:
            # Unknown channel / missing session — surface
            # the dispatcher's diagnostic verbatim.
            return ToolResult(
                content=f"send_message: {e}",
                is_error=True,
            )
        except RuntimeError as e:
            # No IM binding for this user, or no bot
            # registered. Tool-level error so the LLM can
            # react ("no-op push; reply text lands in chat
            # history").
            return ToolResult(
                content=(
                    f"send_message: {e}; the reply will "
                    "land in chat history instead."
                ),
                is_error=True,
            )
        except Exception as e:
            return ToolResult(
                content=f"send_message: send failed: {e}",
                is_error=True,
            )

        return ToolResult(
            content=(
                f"send_message: delivered {len(text)} chars "
                f"to session {ctx.session_id}"
            )
        )

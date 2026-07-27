"""``send_message`` tool — deliver a message to the
operator without leaving the tool loop.

Use case: the LLM is partway through a multi-turn tool
chain (e.g. "read SOUL, list skills, then reply") and
wants to give the user a status update ("Reading your
SOUL...") instead of going silent for the full tool
chain duration. Scheduled tasks also use this tool to
push results to whichever channel the task targets.

Cross-channel delivery (D.28)
-----------------------------

The push target is determined by the **session's channel**
(``chat_sessions.channel``) and dispatched via
``dispatcher.send_to_session(session_id, text)``. The tool
never reads the per-channel IM id itself — that's the
adapter's job.

  - WebUI session (``channel="webui"``) — the dispatcher
    appends the message directly to the chat session store
    so the operator sees it as a chat bubble in the WebUI
    scroll.
  - TG session (``channel="tg"``) — the TG adapter
    resolves the user's bound chat id and pushes via the
    python-telegram-bot client.
  - Scheduled task session (``channel="scheduled"``) —
    the runner creates a session with the task's
    ``target_channel``; the dispatcher routes to the
    corresponding adapter.
  - Future channels (Slack, WeChat, etc.) — write an
    adapter + register it; this tool doesn't change.

The tool is fully channel-agnostic. A single scheduled
task with ``target_channel="tg"`` can deliver to Telegram;
another with ``target_channel="webui"`` delivers to the
WebUI chat scroll. The LLM calls ``send_message`` the
same way regardless.
"""

from __future__ import annotations

import logging
from typing import Any

from magi.agent.tools.base import Tool, ToolContext, ToolResult

logger = logging.getLogger("magi.agent.tools.send_message")


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
    ALLOWED_ROLES = frozenset({"assigned"})
    description = (
        "Deliver a message to the operator without "
        "ending the tool loop. Use sparingly — most "
        "communication should happen in the final reply. "
        "Cross-channel: works for WebUI, Telegram, and "
        "scheduled-task sessions equally. The dispatcher "
        "routes to the session's channel; the tool is "
        "fully channel-agnostic."
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

        # Cross-channel: the dispatcher routes by
        # ``chat_sessions.channel``. WebUI sessions get the
        # message appended directly to the chat store
        # (operator sees it as a chat bubble); TG sessions
        # push via the registered adapter. The LLM gets
        # ``is_error=False`` when the message landed in its
        # intended surface.

        # Hand off to the dispatcher. The dispatcher reads
        # ``chat_sessions.channel`` for ``session_id``, picks
        # the right adapter, and the adapter handles its own
        # IM id resolution + transport. This tool stays
        # channel-agnostic.
        from magi.channels import dispatcher

        logger.info(
            "send_message: dispatching %d chars to session=%s channel=%s",
            len(text), ctx.session_id, ctx.channel,
        )
        try:
            await dispatcher.send_to_session(ctx.session_id, text)
            logger.info("send_message: delivered to session=%s", ctx.session_id)
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

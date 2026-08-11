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
(``chat_sessions.channel``) and dispatched via the channel-
owned delivery worker. The tool never reads the per-channel
IM id itself — that's the adapter's job.

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

Bus plumbing: this tool talks to bus
(:class:`magi.bus.Bus`) via
``ctx.bus.sessions_book`` (cross-contact-safe session
lookup via :meth:`SessionBook.get_for_owner`) and
``ctx.bus.delivery_job_board`` (publish a
:class:`magi.bus.guild.deliveryJob.DeliveryJob` to
the durable ``delivery_outbox`` queue — the channel-owned
DeliveryWorker performs the actual protocol I/O after the
agent transition has committed). The legacy services at
bus Book API and
bus Book API are no longer
imported here.
"""

from __future__ import annotations

import logging
from typing import Any

from magi.bus.guild.deliveryJob import DeliveryJob
from magi.tools.base import Tool, ToolContext, ToolResult

logger = logging.getLogger("magi.tools.comms.send_message")


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
                "description": ("Message body. Up to 4000 characters."),
            },
        },
        "required": ["text"],
    }

    @Tool.require_bus
    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        assert ctx.bus is not None  # guaranteed by @Tool.require_bus
        text = kwargs.get("text")
        if not isinstance(text, str) or not text:
            return ToolResult(
                content="send_message: ``text`` is required and must be a non-empty string",
                is_error=True,
            )
        if len(text) > _MAX_TEXT_LEN:
            return ToolResult(
                content=(f"send_message: text is {len(text)} chars; v0 limit is {_MAX_TEXT_LEN}."),
                is_error=True,
            )

        # Empty session_id means the tool is being called
        # outside a session context (rare — agent-loop test
        # harnesses, edge cases). Surface as a clear error.
        if not ctx.conversation_id:
            return ToolResult(
                content=(
                    "send_message: no session context; "
                    "the LLM must be invoked from inside a "
                    "session for side-channel push."
                ),
                is_error=True,
            )

        # A tool never invokes a channel adapter.  It writes a durable
        # delivery intent; the channel-owned DeliveryWorker performs the
        # actual protocol I/O after the agent transition has committed.
        logger.info(
            "send_message: enqueueing %d chars for session=%s channel=%s",
            len(text),
            ctx.conversation_id,
            ctx.channel,
        )
        try:
            bus = ctx.bus
            # ``get_for_owner`` is the cross-contact-safe lookup:
            # returns ``None`` when the session doesn't belong to
            # ``ctx.contact_id`` even if a future caller ever forgets the
            # gate layer, defence-in-depth over the bare ``get``.
            session = bus.sessions_book.get_for_owner(
                contact_id=int(ctx.contact_id),
                conversation_id=ctx.conversation_id,
            )
            if session is None:
                raise KeyError(f"unknown session {ctx.conversation_id!r}")
            bus.delivery_job_board.publish(
                DeliveryJob(
                    channel=session.channel,
                    destination=session.delivery_address or None,
                    payload={
                        "text": text,
                        "conversation_id": session.conversation_id,
                        "contact_id": session.contact_id,
                    },
                )
            )
            logger.info("send_message: queued for session=%s", ctx.conversation_id)
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
                content=f"send_message: {e}",
                is_error=True,
            )
        except Exception as e:
            return ToolResult(
                content=f"send_message: send failed: {e}",
                is_error=True,
            )

        return ToolResult(
            content=(f"send_message: queued {len(text)} chars to session {ctx.conversation_id}")
        )

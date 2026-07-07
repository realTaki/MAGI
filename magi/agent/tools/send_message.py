"""``send_message`` tool — deliver a side-channel message
to the user without leaving the tool loop.

Use case: the LLM is partway through a multi-turn tool
chain (e.g. "read SOUL, list skills, then reply") and
wants to give the user a status update ("Reading your
SOUL...") instead of going silent for the full tool
chain duration. The Telegram client surfaces this as
an extra reply text; on the WebUI side the message is
emitted into a separate "tool message" channel that the
operator doesn't see directly (it's in the conversation
scrollback only).

Channel dispatch:
  - ``tg``     → ``bot.send_message(chat_id, text)``
  - ``webui``  → returns ``is_error=True``: WebUI users
                 already see the LLM's final reply in the
                 pane; an extra "tool message" would
                 duplicate the chat scroll. v0 disables
                 this tool on webui by design. If you
                 want status updates there, the future
                 feature is "tool events panel" (D.18).

The handler is injected via ``ToolContext`` because the
agent loop owns the TG bot reference (not the tool).
"""

from __future__ import annotations

from typing import Any, Callable, Awaitable

from magi.agent.tools.base import Tool, ToolContext, ToolResult

# Type alias for the optional TG callback. The agent loop
# injects one if the channel is ``"tg"``; for other
# channels it's ``None``.
TGCallback = Callable[[int, str], Awaitable[None]] | None

_MAX_TEXT_LEN = 4000  # matches the TG API limit (4096 with buffer)


class SendMessageTool(Tool):
    """Send a side-channel message to the current user."""

    name = "send_message"
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

        # WebUI: disabled by design (see class docstring).
        if ctx.channel != "tg":
            return ToolResult(
                content=(
                    f"send_message is not available on the "
                    f"``{ctx.channel}`` channel in v0; use the "
                    f"final reply instead."
                ),
                is_error=True,
            )

        # TG: call the callback the agent loop injected.
        # We don't store a python-telegram-bot Bot here
        # because the agent loop owns the reference and we
        # want tool code to stay SDK-agnostic.
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
            # ``ctx.chat_id`` is the string-form chat id we
            # stored at call site. TG needs int.
            chat_id_int = int(ctx.chat_id)
        except (TypeError, ValueError):
            return ToolResult(
                content=(
                    f"send_message: ctx.chat_id {ctx.chat_id!r} is "
                    f"not a valid TG chat id."
                ),
                is_error=True,
            )

        try:
            await callback(chat_id_int, text)
        except Exception as e:
            return ToolResult(
                content=f"send_message: TG send failed: {e}",
                is_error=True,
            )

        return ToolResult(
            content=f"send_message: delivered {len(text)} chars to chat {chat_id_int}"
        )
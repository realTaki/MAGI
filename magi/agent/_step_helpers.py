"""Agent-side request assembly, extracted from the agent worker.

Phase D — the agent turn publishes a :class:`LLMJob` onto the
BUS queue; this module assembles the request payload that the
:class:`magi.providers.worker.ProvidersWorker` consumes. The provider
worker **does not** call into this module — it just deserializes
``system`` / ``messages`` / ``tools`` / ``max_tokens`` from the row.

Two functions:

- :func:`assemble_agent_request` — pure assembly (system prompt,
  history, tool schemas, optional continuation / tool_result /
  steering). Returns ``None`` when the runtime refuses to make the
  call (no configured provider, missing context) so the caller can
  fall back to the canned-reply path.
- :func:`fallback_agent_result` — the canned reply envelope for the
  same fallback paths the agent loop used to send synchronously.

The method itself still calls :func:`magi.agent.compaction.maybe_compact`
so the agent's view of the chat history mirrors today's
behaviour. ``maybe_compact`` itself goes through the providers
queue (Phase D), but the message rewrite happens on the same
in-memory list that we serialize below.
"""

from __future__ import annotations

from typing import Any

from magi.agent import agent_context
from magi.providers.factory import ChatMessage


async def assemble_agent_request(
    *,
    text: str,
    channel: str,
    uid: int | None,
    session_id: str | None,
    caller_role: str | None,
    continuation_messages: list[dict[str, Any]] | None = None,
    tool_results: list[dict[str, Any]] | None = None,
    steering_inputs: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], int] | None:
    """Build the (system, messages, tools, max_tokens) tuple for one inference."""
    if not agent_context.validate_credentials(uid=uid, channel=channel):
        return None
    context = agent_context.build_context(
        text=text, channel=channel, uid=uid,
        session_id=session_id, caller_role=caller_role,
    )
    if context is None:
        return None

    if continuation_messages is not None:
        context.messages = [
            ChatMessage(
                role=item["role"],
                content=item["content"],
                content_blocks=item.get("content_blocks"),
            )
            for item in continuation_messages
        ]
        if tool_results:
            context.messages.append(
                ChatMessage(role="user", content="", content_blocks=tool_results)
            )
        for steering in steering_inputs or ():
            context.messages.append(
                ChatMessage(role="user", content=str(steering.get("text") or ""))
            )

    # Inlining the compact call: ``maybe_compact`` itself goes through
    # the providers queue (Phase D), but the message rewrite happens
    # on the in-memory list we serialize below.
    await agent_context.maybe_compact(uid, session_id, context.messages)

    serialized_messages = [
        {
            "role": m.role,
            "content": m.content,
            **({"content_blocks": m.content_blocks} if m.content_blocks else {}),
        }
        for m in context.messages
    ]
    system = agent_context.build_system_prompt(uid=uid, soul=context.soul)
    return (
        system,
        serialized_messages,
        list(context.tool_schemas),
        agent_context.DEFAULT_MAX_TOKENS,
    )


def fallback_agent_result(reason: str = "agent_fallback") -> dict[str, Any]:
    """Build the canned reply envelope for the fallback paths."""
    return {
        "text": agent_context.fallback_reply(reason),
        "tool_uses": [],
        "assistant_blocks": [],
        "provider": "",
        "model": None,
        "usage": {},
        "messages": [],
    }


__all__ = ["assemble_agent_request", "fallback_agent_result"]

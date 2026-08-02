"""One LLM inference step for the event-driven MAGI actor.

Unlike the legacy ``handle_message`` loop, this module never executes a tool
and never waits for another MAGI.  Its caller persists the returned assistant
blocks and continuation before scheduling any effects.  Keeping the boundary
here makes provider streaming and durable continuations an additive change.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from magi.channels import Channel


@dataclass(frozen=True, slots=True)
class AgentStepResult:
    """The committed outcome of exactly one provider invocation."""

    text: str
    tool_uses: tuple[dict[str, Any], ...]
    assistant_blocks: tuple[dict[str, Any], ...]
    provider: str
    model: str | None
    usage: dict[str, Any]
    messages: tuple[dict[str, Any], ...]


async def run_agent_step(
    state_dir: str,
    *,
    text: str,
    channel: Channel | str,
    uid: int | None,
    session_id: str | None,
    caller_role: str | None,
    max_tokens: int,
    continuation_messages: list[dict[str, Any]] | None = None,
    tool_results: list[dict[str, Any]] | None = None,
    steering_inputs: list[dict[str, Any]] | None = None,
    on_stream_event: Callable[[Any], Awaitable[None]] | None = None,
) -> AgentStepResult:
    """Run one inference and return effects for the actor to persist.

    The existing context builder is deliberately reused during the migration:
    it preserves the current SOUL, memory, role-gated tool schemas, session
    history and compaction behaviour.  No tool is run in this function.
    """
    from magi.agent import loop

    if not loop._validate_credentials(uid=uid, channel=channel):  # noqa: SLF001
        return AgentStepResult(
            text=loop._fallback_reply("agent_no_credentials"),  # noqa: SLF001
            tool_uses=(),
            assistant_blocks=(),
            provider="",
            model=None,
            usage={},
            messages=(),
        )
    context = loop._build_context(  # noqa: SLF001
        state_dir,
        text=text,
        channel=channel,
        uid=uid,
        session_id=session_id,
        caller_role=caller_role,
        max_tool_iterations=None,
    )
    if context is None:
        return AgentStepResult(
            text=loop._fallback_reply(),  # noqa: SLF001
            tool_uses=(),
            assistant_blocks=(),
            provider="",
            model=None,
            usage={},
            messages=(),
        )

    if continuation_messages is not None:
        context.messages = [
            loop.ChatMessage(
                role=item["role"],
                content=item["content"],
                content_blocks=item.get("content_blocks"),
            )
            for item in continuation_messages
        ]
        if tool_results:
            context.messages.append(
                loop.ChatMessage(role="user", content="", content_blocks=tool_results)
            )
        # The provider transcript must close every tool_use before an active
        # run's later human message is added.  The durable bus supplies these
        # in receive order; no channel-specific intent classification occurs
        # here.
        for steering in steering_inputs or ():
            context.messages.append(
                loop.ChatMessage(role="user", content=str(steering.get("text") or ""))
            )
    await loop.maybe_compact(  # noqa: SLF001
        state_dir, uid, session_id, context.messages
    )
    request = {
        "system": loop.build_system_prompt(state_dir, uid=uid, soul=context.soul),  # noqa: SLF001
        "messages": context.messages,
        "max_tokens": max_tokens,
        "tools": context.tool_schemas,
    }
    if on_stream_event is None:
        result = await context.provider.chat(**request)
    else:
        result = await context.provider.stream(**request, on_event=on_stream_event)
    context.messages.append(
        loop.ChatMessage(
            role="assistant", content=result.text or "", content_blocks=result.raw_blocks or None
        )
    )
    return AgentStepResult(
        text=result.text or "",
        tool_uses=tuple(dict(item) for item in result.tool_uses),
        assistant_blocks=tuple(dict(item) for item in (result.raw_blocks or [])),
        provider=context.provider.name,
        model=result.model,
        usage=dict(result.usage or {}),
        messages=tuple(
            {
                "role": message.role,
                "content": message.content,
                "content_blocks": message.content_blocks,
            }
            for message in context.messages
        ),
    )

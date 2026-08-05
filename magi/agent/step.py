"""One LLM inference step for the event-driven MAGI actor.

This module never executes a tool or waits for another MAGI. Its caller persists the returned assistant
blocks and continuation before scheduling any effects.  Keeping the boundary
here makes provider streaming and durable continuations an additive change.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from magi.bus.hooks.contracts import HookAction

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
    *,
    text: str,
    channel: str,
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
    from magi.agent import agent_context

    if not agent_context.validate_credentials(uid=uid, channel=channel):
        return AgentStepResult(
            text=agent_context.fallback_reply("agent_no_credentials"),
            tool_uses=(),
            assistant_blocks=(),
            provider="",
            model=None,
            usage={},
            messages=(),
        )
    context = agent_context.build_context(
        text=text,
        channel=channel,
        uid=uid,
        session_id=session_id,
        caller_role=caller_role,
    )
    if context is None:
        return AgentStepResult(
            text=agent_context.fallback_reply(),
            tool_uses=(),
            assistant_blocks=(),
            provider="",
            model=None,
            usage={},
            messages=(),
        )

    if continuation_messages is not None:
        context.messages = [
            agent_context.ChatMessage(
                role=item["role"],
                content=item["content"],
                content_blocks=item.get("content_blocks"),
            )
            for item in continuation_messages
        ]
        if tool_results:
            context.messages.append(
                agent_context.ChatMessage(role="user", content="", content_blocks=tool_results)
            )
        # The provider transcript must close every tool_use before an active
        # run's later human message is added.  The durable bus supplies these
        # in receive order; no channel-specific intent classification occurs
        # here.
        for steering in steering_inputs or ():
            context.messages.append(
                agent_context.ChatMessage(role="user", content=str(steering.get("text") or ""))
            )
    await agent_context.maybe_compact(
        uid, session_id, context.messages
    )
    request = {
        "system": agent_context.build_system_prompt(uid=uid, soul=context.soul),
        "messages": context.messages,
        "max_tokens": max_tokens,
        "tools": context.tool_schemas,
    }

    # GATE — ``LLM_REQUEST_PREPARED`` is the only hook point
    # that sees the exact provider-bound request the BUS is
    # about to send.  A DENY short-circuits the inference and
    # returns a synthetic fallback so the actor loop resumes.
    decision, request = await _gate_llm_request(
        request=request,
        provider_name=getattr(context.provider, "name", ""),
        model=context.model if hasattr(context, "model") else None,
        uid=uid,
        channel=channel,
        session_id=session_id,
        caller_role=caller_role,
    )
    if decision is HookAction.DENY:
        return AgentStepResult(
            text=agent_context.fallback_reply("agent_llm_request_denied"),
            tool_uses=(),
            assistant_blocks=(),
            provider="",
            model=None,
            usage={},
            messages=(),
        )

    if on_stream_event is None:
        result = await context.provider.chat(**request)
    else:
        result = await context.provider.stream(**request, on_event=on_stream_event)
    context.messages.append(
        agent_context.ChatMessage(
            role="assistant", content=result.text or "", content_blocks=result.raw_blocks or None
        )
    )

    # OBSERVE — ``LLM_RESPONSE_RECEIVED`` is fired *after* the
    # provider call but before the actor commits the transition,
    # so audit handlers see the exact response the BUS recorded.
    # First version is OBSERVE-only (no GATE-DENY on response);
    # a future revision will let a GATE response block the
    # transition (refusal, jailbreak detection, etc.).
    await _observe_llm_response(
        provider_name=getattr(context.provider, "name", ""),
        model=getattr(result, "model", None),
        result=result,
        uid=uid,
        channel=channel,
        session_id=session_id,
        caller_role=caller_role,
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


# ───────────────────────────────────────────────────────────────────── #
# Hook helpers
# ───────────────────────────────────────────────────────────────────── #


async def _gate_llm_request(
    *,
    request: dict[str, Any],
    provider_name: str,
    model: str | None,
    uid: int | None,
    channel: str,
    session_id: str | None,
    caller_role: str | None,
) -> tuple["HookAction", dict[str, Any]]:
    """Run ``LLM_REQUEST_PREPARED`` GATE handlers.

    Returns ``(decision, request)``.  When no handlers are
    registered the decision is ``ALLOW`` and the request is
    returned unchanged.  A handler exception is fail-open; a
    timeout is fail-closed (per the handler's declared
    ``failure_mode``).
    """
    from magi.bus import get_bus
    from magi.bus.hooks.contracts import (
        CausalityHookContext,
        EvaluationRequest,
        HookAction,
        HookDataClassification,
        HookPoint,
        PrincipalHookContext,
        PrincipalType,
        RuntimeHookContext,
        SecurityHookContext,
    )
    from magi.bus.db.base import utcnow_naive

    bus = get_bus()
    hook_service = getattr(bus, "hooks", None)
    if hook_service is None:
        return HookAction.ALLOW, request
    now = utcnow_naive()
    request_metadata = {
        "provider": provider_name,
        "model": model,
        "max_tokens": request.get("max_tokens"),
    }
    eval_request = EvaluationRequest(
        hook_point=HookPoint.LLM_REQUEST_PREPARED,
        subject_type="agent_step",
        subject_id=session_id or "agent_step",
        requested_by="agent.step",
        runtime=RuntimeHookContext(
            magi_id=None, magis_id=None,
            runtime_id="agent-step",
            runtime_instance_id="agent-step",
            environment="runtime",
            workspace_id="default",
        ),
        principal=PrincipalHookContext(
            principal_type=PrincipalType.USER if uid is not None else PrincipalType.SYSTEM,
            principal_id=str(uid) if uid is not None else "system",
            role=caller_role,
            permissions=(),
            membership_id=None,
            source_type=channel,
            source_id=str(uid) if uid is not None else None,
        ),
        causality=CausalityHookContext(
            correlation_id=None, causation_id=None,
            event_id=session_id or "agent_step",
            run_id=session_id or "",
            conversation_id=None, session_id=session_id,
            message_id=None, reply_to=None, external_event_id=None,
        ),
        security=SecurityHookContext(
            attempt=0, deadline=None,
            created_at=now, available_at=now,
            policy_labels=(), security_labels=(),
            data_classification=HookDataClassification.CONFIDENTIAL,
        ),
        metadata=request_metadata,
    )
    try:
        result = await hook_service.evaluate(eval_request)
    except Exception:
        # The hook service is fail-safe; if it crashes we treat
        # the request as allowed so the runtime stays available.
        return HookAction.ALLOW, request
    return result.decision, request


async def _observe_llm_response(
    *,
    provider_name: str,
    model: str | None,
    result: Any,
    uid: int | None,
    channel: str,
    session_id: str | None,
    caller_role: str | None,
) -> None:
    """Fire the ``LLM_RESPONSE_RECEIVED`` observation hook.

    OBSERVE-only; never raises.  A handler crash is logged and
    swallowed because the actor has already received the
    response.
    """
    from magi.bus import get_bus
    from magi.bus.hooks.contracts import (
        CausalityHookContext,
        EvaluationRequest,
        HookDataClassification,
        HookPoint,
        PrincipalHookContext,
        PrincipalType,
        RuntimeHookContext,
        SecurityHookContext,
    )
    from magi.bus.db.base import utcnow_naive

    bus = get_bus()
    hook_service = getattr(bus, "hooks", None)
    if hook_service is None:
        return
    now = utcnow_naive()
    eval_request = EvaluationRequest(
        hook_point=HookPoint.LLM_RESPONSE_RECEIVED,
        subject_type="agent_step",
        subject_id=session_id or "agent_step",
        requested_by="agent.step",
        runtime=RuntimeHookContext(
            magi_id=None, magis_id=None,
            runtime_id="agent-step",
            runtime_instance_id="agent-step",
            environment="runtime",
            workspace_id="default",
        ),
        principal=PrincipalHookContext(
            principal_type=PrincipalType.USER if uid is not None else PrincipalType.SYSTEM,
            principal_id=str(uid) if uid is not None else "system",
            role=caller_role,
            permissions=(),
            membership_id=None,
            source_type=channel,
            source_id=str(uid) if uid is not None else None,
        ),
        causality=CausalityHookContext(
            correlation_id=None, causation_id=None,
            event_id=session_id or "agent_step",
            run_id=session_id or "",
            conversation_id=None, session_id=session_id,
            message_id=None, reply_to=None, external_event_id=None,
        ),
        security=SecurityHookContext(
            attempt=0, deadline=None,
            created_at=now, available_at=now,
            policy_labels=(), security_labels=(),
            data_classification=HookDataClassification.CONFIDENTIAL,
        ),
        metadata={
            "provider": provider_name,
            "model": model,
            "usage": dict(getattr(result, "usage", {}) or {}),
            "finish_reason": getattr(result, "finish_reason", None),
        },
    )
    try:
        await hook_service.publish_observation(eval_request)
    except Exception:
        pass

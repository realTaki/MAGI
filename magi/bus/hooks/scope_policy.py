"""HookPoint → HookDataScope policy.

The static map below is the single source of truth for what data a
handler can request per :class:`magi.bus.hooks.contracts.HookPoint`.
A handler requesting a scope not in this map is rejected at
registration time.

The map is intentionally narrow: a handler subscribed to
``TOOL_CALL_PENDING`` MUST NOT request ``LLM_REQUEST`` — that scope
is only meaningful at ``LLM_REQUEST_PREPARED``.  Without this
policy, a handler that declared ``LLM_REQUEST`` would either get
the LLM request on every tool call (data leak) or get an empty
payload (and silently fail to detect anything).

Adding a new HookPoint requires updating this map; the architecture
test ``test_hook_envelope_purity`` enforces the closure.
"""

from __future__ import annotations

from magi.bus.hooks.contracts import HookDataScope, HookPoint


# Per spec §6 + §8.  Frozen so the registry can rely on identity
# (not just equality) when reasoning about "the same scope set".
HOOK_POINT_ALLOWED_SCOPES: dict[HookPoint, frozenset[HookDataScope]] = {
    # Input lifecycle
    HookPoint.AGENT_INPUT_PENDING: frozenset({
        HookDataScope.RUNTIME_IDENTITY,
        HookDataScope.PRINCIPAL_IDENTITY,
        HookDataScope.CAUSALITY,
        HookDataScope.INPUT_CONTENT,
        HookDataScope.ATTACHMENT_METADATA,
        HookDataScope.RUN_STATE,
    }),
    # LLM request lifecycle — sees the exact provider-bound request
    # and the tool catalog that goes with it.
    HookPoint.LLM_REQUEST_PREPARED: frozenset({
        HookDataScope.RUNTIME_IDENTITY,
        HookDataScope.PRINCIPAL_IDENTITY,
        HookDataScope.CAUSALITY,
        HookDataScope.LLM_REQUEST,
        HookDataScope.TOOL_SCHEMAS,
        HookDataScope.SESSION_WINDOW,
        HookDataScope.MEMORY_MATCHES,
        HookDataScope.RUN_STATE,
    }),
    # LLM response lifecycle — sees the provider's actual returned
    # blocks, not a re-derivation from session state.
    HookPoint.LLM_RESPONSE_RECEIVED: frozenset({
        HookDataScope.RUNTIME_IDENTITY,
        HookDataScope.PRINCIPAL_IDENTITY,
        HookDataScope.CAUSALITY,
        HookDataScope.LLM_RESPONSE,
        HookDataScope.TOOL_SCHEMAS,
        HookDataScope.RUN_STATE,
    }),
    # Tool call lifecycle — sees the durable tool job (arguments,
    # schema hash, caller identity) and the catalog snapshot the
    # agent used to choose the tool.
    HookPoint.TOOL_CALL_PENDING: frozenset({
        HookDataScope.RUNTIME_IDENTITY,
        HookDataScope.PRINCIPAL_IDENTITY,
        HookDataScope.CAUSALITY,
        HookDataScope.TOOL_CALL,
        HookDataScope.TOOL_SCHEMAS,
        HookDataScope.RUN_STATE,
    }),
    # Tool result lifecycle — sees the executor's normalised result
    # and metadata; the raw result is NOT re-exposed to LLM_REQUEST
    # unless a future hook point decides that policy.
    HookPoint.TOOL_RESULT_RECEIVED: frozenset({
        HookDataScope.RUNTIME_IDENTITY,
        HookDataScope.CAUSALITY,
        HookDataScope.TOOL_RESULT,
        HookDataScope.RUN_STATE,
    }),
    # Multi-agent lifecycle
    HookPoint.A2A_INVOCATION_PENDING: frozenset({
        HookDataScope.RUNTIME_IDENTITY,
        HookDataScope.PRINCIPAL_IDENTITY,
        HookDataScope.CAUSALITY,
        HookDataScope.A2A_INVOCATION,
        HookDataScope.RUN_STATE,
    }),
    HookPoint.A2A_RESULT_RECEIVED: frozenset({
        HookDataScope.RUNTIME_IDENTITY,
        HookDataScope.PRINCIPAL_IDENTITY,
        HookDataScope.CAUSALITY,
        HookDataScope.A2A_RESULT,
        HookDataScope.RUN_STATE,
    }),
    # Delivery lifecycle — sees the rendered message exactly as it
    # would be sent to the channel adapter.
    HookPoint.DELIVERY_PENDING: frozenset({
        HookDataScope.RUNTIME_IDENTITY,
        HookDataScope.PRINCIPAL_IDENTITY,
        HookDataScope.CAUSALITY,
        HookDataScope.DELIVERY_PAYLOAD,
        HookDataScope.RUN_STATE,
    }),
    # Delivery completion — fires after the delivery has been sent
    # (status="delivered" / "dead").  The payload is already in the
    # wild so this is OBSERVE-only and does not expose
    # DELIVERY_PAYLOAD; handlers get status + attempts + run_state.
    HookPoint.DELIVERY_DISPATCHED: frozenset({
        HookDataScope.RUNTIME_IDENTITY,
        HookDataScope.CAUSALITY,
        HookDataScope.RUN_STATE,
    }),
    # Run-state observation hook
    HookPoint.RUN_TRANSITION_COMMITTED: frozenset({
        HookDataScope.RUNTIME_IDENTITY,
        HookDataScope.PRINCIPAL_IDENTITY,
        HookDataScope.CAUSALITY,
        HookDataScope.RUN_STATE,
    }),
    # Operational hooks — minimal data so the handler can alert
    # without scraping other tables.
    HookPoint.OPERATION_FAILED: frozenset({
        HookDataScope.RUNTIME_IDENTITY,
        HookDataScope.CAUSALITY,
        HookDataScope.OPERATION_ERROR,
        HookDataScope.RUN_STATE,
    }),
    HookPoint.OPERATION_DEAD_LETTERED: frozenset({
        HookDataScope.RUNTIME_IDENTITY,
        HookDataScope.CAUSALITY,
        HookDataScope.OPERATION_ERROR,
        HookDataScope.RUN_STATE,
    }),
}


def allowed_scopes_for(hook_point: HookPoint) -> frozenset[HookDataScope]:
    """Return the set of scopes the given HookPoint permits.

    Raises ``KeyError`` if the hook point has no policy entry —
    that means a new HookPoint was added without a corresponding
    scope policy, which is a configuration bug.
    """
    return HOOK_POINT_ALLOWED_SCOPES[hook_point]


def scope_granted_for_registration(
    registration: "HookRegistrationLike",
) -> tuple[HookDataScope, ...]:
    """Return the (deduplicated, sorted) scopes a registration can claim.

    The registry calls this with each registered handler and
    rejects the registration if any requested scope is not in
    :func:`allowed_scopes_for` for every subscribed HookPoint.
    """
    scopes: set[HookDataScope] = set()
    for point in registration.hook_points:
        scopes.update(HOOK_POINT_ALLOWED_SCOPES[point])
    return tuple(sorted(scopes, key=lambda s: s.value))


class HookRegistrationLike:
    """Type-narrow shim — the registry accepts any object with the
    shape below.  Defined as a class with annotations so static
    type-checkers see the same fields as
    :class:`magi.bus.hooks.contracts.HookRegistration`.
    """

    hook_points: tuple[HookPoint, ...]
    required_scopes: frozenset[HookDataScope]


__all__ = [
    "HOOK_POINT_ALLOWED_SCOPES",
    "allowed_scopes_for",
    "scope_granted_for_registration",
]

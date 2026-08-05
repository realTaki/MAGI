"""BUS Hook subsystem.

Hook handlers receive a frozen, JSON-safe :class:`HookEnvelope` that
the BUS materialises from the data-scopes each handler declared at
registration time. Handlers never receive a :class:`Bus` reference or
any other queryable handle — the envelope is the only input. This is
the security boundary: a handler that asks for the LLM request sees
the LLM request, nothing else; a handler that asks for tool
arguments sees tool arguments, nothing else.

The lifecycle for every GATE point is::

    short_txn: persist candidate + create pending hook_evaluation
        -> commit
    outside_txn: executor.run_handlers(envelope) with timeout + failure_mode
    short_txn: persist HookDecision + update candidate state
        -> commit
    external side-effect: only fires if final decision == ALLOW

OBSERVE hooks follow the same transaction boundary but never block
the original operation.
"""

from magi.bus.hooks.aggregation import aggregate_decisions
from magi.bus.hooks.contracts import (
    CausalityHookContext,
    HookAction,
    HookDataClassification,
    HookDataScope,
    HookDecision,
    HookEnvelope,
    HookEvaluation,
    HookEvaluationResult,
    HookEvaluationStatus,
    HookFailureMode,
    HookHandlerProtocol,
    HookMode,
    HookPoint,
    HookRegistration,
    HookSubject,
    JsonValue,
    PrincipalHookContext,
    PrincipalType,
    RuntimeHookContext,
    SecurityHookContext,
    TruncationMarker,
)
from magi.bus.hooks.executor import HookExecutor
from magi.bus.hooks.hooks_service_init import install_hooks_into_bus
from magi.bus.hooks.materializers import HookEnvelopeMaterializer
from magi.bus.hooks.redaction import SecretRedactor
from magi.bus.hooks.registry import HookRegistry
from magi.bus.hooks.repository import HookEvaluationRepository
from magi.bus.hooks.scope_policy import HOOK_POINT_ALLOWED_SCOPES, allowed_scopes_for
from magi.bus.hooks.service import HookService
from magi.bus.hooks.truncation import (
    MAX_ATTACHMENT_METADATA,
    MAX_ENVELOPE_BYTES,
    MAX_FIELD_BYTES,
    MAX_MEMORY_MATCHES,
    MAX_SESSION_WINDOW_MESSAGES,
    TruncationContext,
    apply_size_caps,
)


__all__ = [
    # contracts
    "CausalityHookContext",
    "HookAction",
    "HookDataClassification",
    "HookDataScope",
    "HookDecision",
    "HookEnvelope",
    "HookEvaluation",
    "HookEvaluationResult",
    "HookEvaluationStatus",
    "HookFailureMode",
    "HookHandlerProtocol",
    "HookMode",
    "HookPoint",
    "HookRegistration",
    "HookSubject",
    "JsonValue",
    "PrincipalHookContext",
    "PrincipalType",
    "RuntimeHookContext",
    "SecurityHookContext",
    "TruncationMarker",
    # registry + scope policy
    "HookRegistry",
    "HOOK_POINT_ALLOWED_SCOPES",
    "allowed_scopes_for",
    # materializer + redaction + truncation + aggregation
    "HookEnvelopeMaterializer",
    "SecretRedactor",
    "TruncationContext",
    "TruncationMarker",
    "apply_size_caps",
    "MAX_ENVELOPE_BYTES",
    "MAX_FIELD_BYTES",
    "MAX_SESSION_WINDOW_MESSAGES",
    "MAX_MEMORY_MATCHES",
    "MAX_ATTACHMENT_METADATA",
    "aggregate_decisions",
    # executor + service + persistence
    "HookExecutor",
    "HookEvaluationRepository",
    "HookService",
    # composition-root helper
    "install_hooks_into_bus",
]

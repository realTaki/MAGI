"""Hook subsystem contracts.

Every type here is a frozen dataclass with ``slots=True`` — no Pydantic,
no mutable state.  The contracts are the only types a hook handler
ever sees: it receives a :class:`HookEnvelope` and may return a
:class:`HookDecision`.  Nothing else from ``magi.bus`` crosses the
boundary.

The contracts intentionally live separately from
``magi.bus.protocols.*`` (the existing bus DTOs) so the architecture
test can target ``magi.bus.hooks.contracts`` in isolation without
pulling in ORM models or session types.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


# ───────────────────────────────────────────────────────────────────── #
# Enums
# ───────────────────────────────────────────────────────────────────── #


class HookPoint(str, enum.Enum):
    """Stable identifiers for every hook point in the runtime.

    Adding a new hook point:

      1. Add the value here.
      2. Add the value to :data:`magi.bus.hooks.scope_policy.HOOK_POINT_ALLOWED_SCOPES`.
      3. Add a materializer in :mod:`magi.bus.hooks.materializers`.
      4. Wire the emit site at the appropriate insertion point.

    The string value is what the persistence layer stores; do not
    rename without an Alembic migration.
    """

    # Input lifecycle
    AGENT_INPUT_PENDING = "agent.input.pending"
    # LLM lifecycle
    LLM_REQUEST_PREPARED = "llm.request.prepared"
    LLM_RESPONSE_RECEIVED = "llm.response.received"
    # Tool lifecycle
    TOOL_CALL_PENDING = "tool.call.pending"
    TOOL_RESULT_RECEIVED = "tool.result.received"
    # Multi-agent lifecycle
    A2A_INVOCATION_PENDING = "a2a.invocation.pending"
    A2A_RESULT_RECEIVED = "a2a.result.received"
    # Delivery lifecycle
    DELIVERY_PENDING = "delivery.pending"
    # Run-state lifecycle
    RUN_TRANSITION_COMMITTED = "run.transition.committed"
    # Operational hooks (OBSERVE-only)
    OPERATION_FAILED = "operation.failed"
    OPERATION_DEAD_LETTERED = "operation.dead_lettered"


class HookMode(str, enum.Enum):
    """Whether the handler observes or gates the underlying operation.

    OBSERVE handlers never block the original operation; they may
    return ``None`` from ``handle()``.  GATE handlers MUST return a
    :class:`HookDecision` whose ``action`` is one of
    :class:`HookAction`'s values; failure to do so is treated as
    fail-open or fail-closed per the registration's
    :class:`HookFailureMode`.
    """

    OBSERVE = "observe"
    GATE = "gate"


class HookAction(str, enum.Enum):
    """The structural decision a GATE handler can return.

    Execution logic depends ONLY on the :class:`HookAction` value —
    never on a free-form ``reason_code`` or ``message``.  Future
    actions (``QUARANTINE``, ``REQUIRE_APPROVAL``, ``REWRITE``,
    ``DEFER``) will extend this enum; first version ships with
    ``ALLOW`` / ``DENY`` only.
    """

    ALLOW = "allow"
    DENY = "deny"


class HookFailureMode(str, enum.Enum):
    """How the executor treats a handler timeout or exception.

    ``FAIL_OPEN`` — the runtime records the error and continues as
    though the handler returned ALLOW.  Use for handlers whose
    unavailability must not block operations (audit, metrics).

    ``FAIL_CLOSED`` — the runtime treats the error as a DENY.  Use
    for GATE handlers that must hold the line when they cannot make
    a confident decision (Prompt Injection, data exfiltration).
    """

    FAIL_OPEN = "fail_open"
    FAIL_CLOSED = "fail_closed"


class HookEvaluationStatus(str, enum.Enum):
    """Lifecycle status of one :class:`HookEvaluation` row."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    ERRORED = "errored"


class HookDataClassification(str, enum.Enum):
    """Per-field data sensitivity classification.

    Used by the redactor to decide whether to drop, redact, hash, or
    preserve a field.  See :mod:`magi.bus.hooks.redaction`.
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    SECRET = "secret"
    CREDENTIAL = "credential"


class HookDataScope(str, enum.Enum):
    """The data scopes a handler can declare at registration.

    The BUS grants a handler ONLY the scopes it declares.  Each
    :class:`HookPoint` permits a fixed subset of scopes (see
    :data:`magi.bus.hooks.scope_policy.HOOK_POINT_ALLOWED_SCOPES`);
    requesting a scope the point does not permit is a registration
    error.
    """

    RUNTIME_IDENTITY = "runtime.identity"
    PRINCIPAL_IDENTITY = "principal.identity"
    CAUSALITY = "causality"

    INPUT_CONTENT = "input.content"
    ATTACHMENT_METADATA = "attachment.metadata"

    SESSION_WINDOW = "session.window"
    MEMORY_MATCHES = "memory.matches"

    LLM_REQUEST = "llm.request"
    LLM_RESPONSE = "llm.response"
    TOOL_SCHEMAS = "tool.schemas"

    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"

    A2A_INVOCATION = "a2a.invocation"
    A2A_RESULT = "a2a.result"

    DELIVERY_PAYLOAD = "delivery.payload"
    RUN_STATE = "run.state"
    OPERATION_ERROR = "operation.error"


class PrincipalType(str, enum.Enum):
    """Who initiated the operation the hook is evaluating."""

    USER = "user"
    ADMIN = "admin"
    EMPLOYEE = "employee"
    VISITOR = "visitor"
    MAGI = "magi"
    SYSTEM = "system"
    PLUGIN = "plugin"
    TASK = "task"
    CHANNEL = "channel"


# ───────────────────────────────────────────────────────────────────── #
# Common data (every envelope carries these)
# ───────────────────────────────────────────────────────────────────── #


@dataclass(frozen=True, slots=True)
class RuntimeHookContext:
    """Stable identity of the runtime that emitted the hook.

    Identifies the physical BUS instance so a hook installed on one
    runtime can never be confused with one installed on another.
    """

    magi_id: int | None
    magis_id: int | None
    runtime_id: str
    runtime_instance_id: str
    environment: str
    workspace_id: str


@dataclass(frozen=True, slots=True)
class PrincipalHookContext:
    """Who caused the operation the hook is observing."""

    principal_type: PrincipalType
    principal_id: str
    role: str | None
    permissions: tuple[str, ...]
    membership_id: str | None
    source_type: str | None
    source_id: str | None


@dataclass(frozen=True, slots=True)
class CausalityHookContext:
    """Trace identifiers that correlate this hook with upstream events."""

    correlation_id: str | None
    causation_id: str | None
    event_id: str
    run_id: str
    conversation_id: str | None
    session_id: str | None
    message_id: str | None
    reply_to: str | None
    external_event_id: str | None


@dataclass(frozen=True, slots=True)
class SecurityHookContext:
    """Per-evaluation execution metadata.

    Carries policy / data labels so handlers can make context-aware
    decisions without re-querying the BUS.  Does not expose lease
    owner or low-level lock state; that surfaces via the operational
    hooks only.
    """

    attempt: int
    deadline: datetime | None
    created_at: datetime
    available_at: datetime
    policy_labels: tuple[str, ...]
    security_labels: tuple[str, ...]
    data_classification: HookDataClassification


@dataclass(frozen=True, slots=True)
class HookSubject:
    """The BUS record the hook is observing.

    ``subject_type`` is one of ``"agent_inbox"``, ``"llm_attempt"``,
    ``"tool_job"``, ``"a2a_invocation"``, ``"delivery_outbox"``,
    ``"agent_run"``, etc.  ``subject_id`` is the row's stable id.
    """

    subject_type: str
    subject_id: str


@dataclass(frozen=True, slots=True)
class TruncationMarker:
    """Sentinel attached to truncated payloads.

    The materializer emits this alongside ``payload`` /
    ``context`` fields when a size cap kicks in.  Handlers see
    ``original_size``, ``included_size`` and a ``content_hash`` so
    they can recognise the cut without re-hashing the full payload.
    """

    truncated: bool
    original_size: int
    included_size: int
    content_hash: str


# ───────────────────────────────────────────────────────────────────── #
# Envelope — the only thing handlers ever receive
# ───────────────────────────────────────────────────────────────────── #


# ``JsonValue`` is the recursive JSON-safe value type.  We do not use
# a ``TypeAlias`` for it because the existing codebase relies on
# ``typing.Any`` for JSON-shaped trees in the queue layer; the alias
# is here for documentation / IDE hints, not enforcement.
JsonValue = Any


@dataclass(frozen=True, slots=True)
class HookEnvelope:
    """Immutable, JSON-safe snapshot handed to one hook handler.

    Every field is JSON-safe: no ORM models, no SQLAlchemy sessions,
    no Provider clients, no Channel adapters, no callbacks, no
    plaintext secrets.  See the architecture test
    ``test_hook_envelope_purity.py`` for the property check.

    The envelope is the *only* input a :class:`HookHandlerProtocol`
    receives.  Handlers MUST NOT receive a :class:`Bus` reference or
    any other queryable handle.
    """

    schema_version: str
    hook_event_id: str
    hook_point: HookPoint
    occurred_at: datetime

    runtime: RuntimeHookContext
    principal: PrincipalHookContext
    causality: CausalityHookContext
    subject: HookSubject

    payload: JsonValue
    context: Mapping[str, JsonValue]
    security: SecurityHookContext
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)


# ───────────────────────────────────────────────────────────────────── #
# Decision + Evaluation + Result
# ───────────────────────────────────────────────────────────────────── #


@dataclass(frozen=True, slots=True)
class HookDecision:
    """One handler's verdict on one :class:`HookEnvelope`.

    Execution logic reads ONLY :attr:`action` — the other fields are
    for audit and for handlers that want to chain their own
    decisions.  Returning a :class:`HookDecision` from an OBSERVE
    handler is allowed but its ``action`` is ignored.
    """

    hook_id: str
    hook_version: str
    hook_event_id: str
    action: HookAction
    reason_code: str | None = None
    message: str | None = None
    labels: tuple[str, ...] = ()
    risk_score: float | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HookEvaluation:
    """Persisted audit row for one evaluation.

    The unique key is ``(hook_event_id, hook_id, hook_version)`` —
    re-evaluation of the same envelope by the same handler version
    returns the cached row instead of re-running.
    """

    hook_event_id: str
    hook_id: str
    hook_version: str
    hook_point: HookPoint
    subject_type: str
    subject_id: str
    mode: HookMode
    failure_mode: HookFailureMode
    input_digest: str
    requested_scopes: tuple[HookDataScope, ...]
    decision: HookAction | None
    reason_code: str | None
    labels: tuple[str, ...]
    risk_score: float | None
    status: HookEvaluationStatus
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None
    error_type: str | None
    sanitized_error: str | None
    attempt_count: int
    created_at: datetime
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HookEvaluationResult:
    """Final outcome of an ``evaluate()`` call.

    Returned to the caller of :meth:`HookService.evaluate` and
    checked to decide whether to proceed with the original
    operation.  The aggregated :attr:`decision` follows the rules
    in spec §11 (any GATE-DENY wins; OBSERVE handlers do not
    affect the decision; failure modes are honoured).
    """

    hook_event_id: str
    hook_point: HookPoint
    decision: HookAction
    decisions: tuple[HookDecision, ...]
    reason_codes: tuple[str, ...]
    evaluations: tuple[HookEvaluation, ...]
    duration_ms: int


# ───────────────────────────────────────────────────────────────────── #
# Registration + handler protocol
# ───────────────────────────────────────────────────────────────────── #


@dataclass(frozen=True, slots=True)
class HookRegistration:
    """What a handler declares about itself when subscribing.

    ``required_scopes`` MUST be a subset of
    :func:`magi.bus.hooks.scope_policy.allowed_scopes_for` for every
    :class:`HookPoint` in :attr:`hook_points`; the registry rejects
    any registration that violates the policy.
    """

    hook_id: str
    hook_version: str
    hook_points: tuple[HookPoint, ...]
    mode: HookMode
    priority: int
    required_scopes: frozenset[HookDataScope]
    timeout_ms: int
    failure_mode: HookFailureMode
    enabled: bool = True
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)


@runtime_checkable
class HookHandlerProtocol(Protocol):
    """The interface every hook plugin implements.

    Handlers receive ONLY the :class:`HookEnvelope`.  Returning a
    :class:`HookDecision` is required for GATE handlers and
    optional for OBSERVE handlers (return ``None`` to skip).
    """

    async def handle(self, envelope: HookEnvelope) -> HookDecision | None: ...


__all__ = [
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
]

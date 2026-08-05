"""HookService — the ``bus.hooks`` façade.

The service is the single entry point the runtime uses to:

  - Register / unregister handlers (``register_handler``,
    ``unregister_handler``).
  - Evaluate one :class:`HookPoint` against the registered
    handlers (``evaluate``).
  - Publish a non-blocking :class:`HookPoint` observation
    (``publish_observation``).
  - Read back evaluations for the WebUI knowledge page
    (``get_evaluation``, ``list_evaluations``).

Every public method is async because handlers are async, but the
non-handler methods are thin wrappers over the repository /
registry and stay cheap.

Lifecycle
---------

The service is constructed once at composition-root time by
:meth:`install_hooks_into_bus` and shared across the process via
the :class:`Bus` façade.  Tests can construct a fresh service
per-test for isolation.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from magi.bus.db.base import utcnow_naive
from magi.bus.hooks.aggregation import aggregate_decisions
from magi.bus.hooks.contracts import (
    CausalityHookContext,
    HookAction,
    HookDataScope,
    HookDecision,
    HookEnvelope,
    HookEvaluation,
    HookEvaluationResult,
    HookEvaluationStatus,
    HookHandlerProtocol,
    HookMode,
    HookPoint,
    HookRegistration,
    HookSubject,
    PrincipalHookContext,
    PrincipalType,
    RuntimeHookContext,
    SecurityHookContext,
)
from magi.bus.hooks.executor import HookExecutor
from magi.bus.hooks.materializers import HookEnvelopeMaterializer
from magi.bus.hooks.registry import HookRegistrationError, HookRegistry, RegisteredHandler
from magi.bus.hooks.repository import (
    CompletionUpdate,
    HookEvaluationRepository,
    PendingEvaluation,
)


logger = logging.getLogger("magi.bus.hooks.service")


# ───────────────────────────────────────────────────────────────────── #
# Inputs to evaluate()
# ───────────────────────────────────────────────────────────────────── #


@dataclass(frozen=True, slots=True)
class EvaluationRequest:
    """Inputs the caller supplies to :meth:`HookService.evaluate`.

    ``requested_by`` is a free-form identifier (worker_id,
    actor name) used for audit only — it never enters the
    handler envelope and the runtime does NOT authenticate it.
    """

    hook_point: HookPoint
    subject_type: str
    subject_id: str
    requested_by: str | None = None
    runtime: RuntimeHookContext | None = None
    principal: PrincipalHookContext | None = None
    causality: CausalityHookContext | None = None
    security: SecurityHookContext | None = None
    metadata: Mapping[str, Any] | None = None


# ───────────────────────────────────────────────────────────────────── #
# Service
# ───────────────────────────────────────────────────────────────────── #


class HookService:
    """BUS-owned façade for the hook subsystem.

    Combines:

      - :class:`HookRegistry` — in-memory handler map.
      - :class:`HookEvaluationRepository` — durable audit rows.
      - :class:`HookEnvelopeMaterializer` — scope-based projections.
      - :class:`HookExecutor` — async handler runner.

    Construction is cheap — no DB / network IO until the first
    call.  The composition root wires the four collaborators
    together at boot.
    """

    def __init__(
        self,
        *,
        registry: HookRegistry | None = None,
        repository: HookEvaluationRepository | None = None,
        materializer: HookEnvelopeMaterializer | None = None,
        executor: HookExecutor | None = None,
    ) -> None:
        self._registry = registry or HookRegistry()
        self._repository = repository or HookEvaluationRepository()
        self._materializer = materializer or HookEnvelopeMaterializer(
            state_dir=None,
        )
        self._executor = executor or HookExecutor()

    # -- registration -------------------------------------------------- #

    def register_handler(
        self,
        registration: HookRegistration,
        handler: HookHandlerProtocol,
    ) -> RegisteredHandler:
        """Validate and install ``handler``.

        Raises :class:`HookRegistrationError` if the registration
        violates the policy.
        """
        return self._registry.register(registration, handler)

    def unregister_handler(self, hook_id: str) -> None:
        self._registry.unregister(hook_id)

    def enable_handler(self, hook_id: str) -> None:
        self._registry.enable(hook_id)

    def disable_handler(self, hook_id: str) -> None:
        self._registry.disable(hook_id)

    def list_handlers(self) -> tuple[RegisteredHandler, ...]:
        return self._registry.all_handlers()

    # -- evaluation ---------------------------------------------------- #

    async def evaluate(
        self,
        request: EvaluationRequest,
    ) -> HookEvaluationResult:
        """Run every registered handler subscribed to ``hook_point``.

        Algorithm:

          1. Look up subscribed handlers in registry priority order.
          2. For each handler, look up an existing
             ``(hook_event_id, hook_id, hook_version)`` row.
             If found and terminal, reuse the cached decision.
             Otherwise, insert a pending row, run the handler,
             then update the row to terminal.
          3. Aggregate the decisions per spec §11.
          4. Return the :class:`HookEvaluationResult`.

        The function NEVER raises — every failure is converted to
        a fail-open / fail-closed decision per the registration.
        """
        hook_event_id = _derive_hook_event_id(
            request.hook_point,
            request.subject_type,
            request.subject_id,
        )
        handlers = self._registry.handlers_for(request.hook_point)
        runtime = request.runtime or _default_runtime()
        principal = request.principal or _default_principal()
        causality = request.causality or _default_causality(
            request.subject_type, request.subject_id,
        )
        security = request.security or _default_security()

        if not handlers:
            # No handlers — short-circuit without persisting.
            return HookEvaluationResult(
                hook_event_id=hook_event_id,
                hook_point=request.hook_point,
                decision=HookAction.ALLOW,
                decisions=(),
                reason_codes=(),
                evaluations=(),
                duration_ms=0,
            )

        # Materialize the envelope ONCE per evaluation.  Handlers
        # share the same envelope object — they cannot mutate it
        # (frozen dataclass), so concurrent evaluation of the
        # same subject by two handlers is safe.
        # Each handler's ``required_scopes`` are projected
        # independently to avoid leaking data to a handler that
        # did not ask for it.
        evaluations: list[HookEvaluation] = []
        outcomes: list[Any] = []
        started = time.monotonic()

        # The envelope is materialised per-handler so the scope
        # projection matches what the registration declared.
        # The BUS materializer is cheap; running it per-handler
        # is the cost of honouring the spec's "handler sees only
        # the scopes it asked for" invariant.
        for registered in handlers:
            scopes = registered.projected_scopes
            envelope = self._materializer.materialize(
                hook_event_id=hook_event_id,
                hook_point=request.hook_point,
                subject=HookSubject(
                    subject_type=request.subject_type,
                    subject_id=request.subject_id,
                ),
                requested_scopes=scopes,
                runtime=runtime,
                principal=principal,
                causality=causality,
                security=security,
                metadata=request.metadata or {},
            )
            input_digest = _digest_envelope(envelope)
            cached = self._repository.get(
                hook_event_id,
                registered.registration.hook_id,
                registered.registration.hook_version,
            )
            if cached is not None and cached.status in {
                HookEvaluationStatus.COMPLETED,
                HookEvaluationStatus.FAILED,
                HookEvaluationStatus.TIMED_OUT,
                HookEvaluationStatus.ERRORED,
            }:
                evaluations.append(cached)
                outcomes.append(_outcome_from_cached(cached, registered))
                continue

            self._repository.insert_pending(
                PendingEvaluation(
                    hook_event_id=hook_event_id,
                    hook_id=registered.registration.hook_id,
                    hook_version=registered.registration.hook_version,
                    hook_point=request.hook_point,
                    subject_type=request.subject_type,
                    subject_id=request.subject_id,
                    mode=registered.registration.mode,
                    failure_mode=registered.registration.failure_mode.value,
                    input_digest=input_digest,
                    requested_scopes=registered.projected_scopes,
                    attempt=0,
                    metadata=request.metadata or {},
                )
            )
            self._repository.mark_running(
                hook_event_id,
                registered.registration.hook_id,
                registered.registration.hook_version,
            )
            handler_outcomes = await self._executor.run_handlers(
                (registered,), envelope,
            )
            outcome = handler_outcomes[0]
            outcomes.append(outcome)
            completed = self._repository.mark_terminal(
                CompletionUpdate(
                    hook_event_id=hook_event_id,
                    hook_id=registered.registration.hook_id,
                    hook_version=registered.registration.hook_version,
                    status=_status_from_outcome(outcome),
                    decision=outcome.decision.action.value,
                    reason_code=outcome.decision.reason_code,
                    labels=outcome.decision.labels,
                    risk_score=outcome.decision.risk_score,
                    duration_ms=_duration_ms_from_outcome(outcome),
                    error_type=outcome.error_type,
                    sanitized_error=outcome.sanitized_error,
                    metadata=dict(outcome.decision.metadata or {}),
                )
            )
            if completed is not None:
                evaluations.append(completed)

        action, decisions, reason_codes = aggregate_decisions(tuple(outcomes))
        duration_ms = int((time.monotonic() - started) * 1000)
        return HookEvaluationResult(
            hook_event_id=hook_event_id,
            hook_point=request.hook_point,
            decision=action,
            decisions=tuple(decisions),
            reason_codes=tuple(reason_codes),
            evaluations=tuple(evaluations),
            duration_ms=duration_ms,
        )

    async def publish_observation(
        self,
        request: EvaluationRequest,
    ) -> str:
        """Run only OBSERVE handlers for ``hook_point``.

        GATE handlers are skipped — observation never blocks.
        Returns the ``hook_event_id`` for the trace.
        """
        hook_event_id = _derive_hook_event_id(
            request.hook_point,
            request.subject_type,
            request.subject_id,
        )
        observe_only = tuple(
            h for h in self._registry.handlers_for(request.hook_point)
            if h.registration.mode is HookMode.OBSERVE
        )
        if not observe_only:
            return hook_event_id
        # Reuse :meth:`evaluate` by constructing a synthetic
        # request — but the GATE handlers must already be
        # filtered out.  Easiest path: build a temp registry.
        from magi.bus.hooks.registry import HookRegistry

        temp_registry = HookRegistry()
        for h in observe_only:
            temp_registry.register(h.registration, h.handler)
        temp_service = HookService(
            registry=temp_registry,
            repository=self._repository,
            materializer=self._materializer,
            executor=self._executor,
        )
        await temp_service.evaluate(request)
        return hook_event_id

    # -- read-back ----------------------------------------------------- #

    def get_evaluation(
        self,
        hook_event_id: str,
        hook_id: str,
        hook_version: str,
    ) -> HookEvaluation | None:
        return self._repository.get(hook_event_id, hook_id, hook_version)

    def get_event(self, hook_event_id: str) -> tuple[HookEvaluation, ...]:
        return self._repository.get_event(hook_event_id)

    def list_evaluations(
        self,
        *,
        hook_point: HookPoint | None = None,
        limit: int = 50,
    ) -> tuple[HookEvaluation, ...]:
        return self._repository.list_recent(hook_point=hook_point, limit=limit)

    def list_by_subject(
        self,
        subject_type: str,
        subject_id: str,
        *,
        limit: int = 50,
    ) -> tuple[HookEvaluation, ...]:
        return self._repository.list_by_subject(
            subject_type, subject_id, limit=limit,
        )


# ───────────────────────────────────────────────────────────────────── #
# Helpers
# ───────────────────────────────────────────────────────────────────── #


def _derive_hook_event_id(
    hook_point: HookPoint, subject_type: str, subject_id: str,
) -> str:
    """Derive a stable ``hook_event_id`` from the subject triple.

    The same (hook_point, subject_type, subject_id) always
    produces the same id, which is what idempotency depends on.
    For re-evaluations we always look up by this id; if the row
    is terminal the cached decision wins.
    """
    digest = hashlib.sha256(
        f"{hook_point.value}|{subject_type}|{subject_id}".encode("utf-8"),
    ).hexdigest()[:16]
    return f"hkevt_{digest}"


def _digest_envelope(envelope: HookEnvelope) -> str:
    """Return a stable digest of the envelope's projected payload.

    Used as ``input_digest`` on the evaluation row so re-runs
    with identical inputs are easy to identify in audit.
    """
    blob = (
        f"{envelope.hook_point.value}|"
        f"{envelope.subject.subject_type}|"
        f"{envelope.subject.subject_id}|"
        f"{sorted(envelope.payload.keys()) if isinstance(envelope.payload, dict) else ''}"
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:32]


def _outcome_from_cached(cached: HookEvaluation, registered: RegisteredHandler):
    """Reconstruct a :class:`HandlerOutcome` from a cached row."""
    from magi.bus.hooks.aggregation import HandlerOutcome

    decision = HookDecision(
        hook_id=cached.hook_id,
        hook_version=cached.hook_version,
        hook_event_id=cached.hook_event_id,
        action=HookAction(cached.decision) if cached.decision else HookAction.ALLOW,
        reason_code=cached.reason_code,
        labels=cached.labels,
        risk_score=cached.risk_score,
    )
    return HandlerOutcome(
        handler=registered,
        decision=decision,
        error_type=cached.error_type,
        sanitized_error=cached.sanitized_error,
        timed_out=cached.status is HookEvaluationStatus.TIMED_OUT,
    )


def _status_from_outcome(outcome) -> HookEvaluationStatus:
    if outcome.timed_out:
        return HookEvaluationStatus.TIMED_OUT
    if outcome.error_type is not None:
        return HookEvaluationStatus.ERRORED
    return HookEvaluationStatus.COMPLETED


def _duration_ms_from_outcome(outcome) -> int:
    """Best-effort duration estimate from the outcome metadata.

    The executor doesn't carry a start-time per handler (it
    would have to track it just for this metric); for the
    first version we approximate with a small constant when
    no explicit measurement is available.  Subsequent
    versions can pass a ``started_at`` through the outcome.
    """
    md = outcome.decision.metadata or {}
    if isinstance(md, Mapping) and "duration_ms" in md:
        try:
            return int(md["duration_ms"])
        except (TypeError, ValueError):
            pass
    return 0


def _default_runtime() -> RuntimeHookContext:
    return RuntimeHookContext(
        magi_id=None,
        magis_id=None,
        runtime_id="runtime",
        runtime_instance_id=str(uuid.uuid4()),
        environment="dev",
        workspace_id="default",
    )


def _default_principal() -> PrincipalHookContext:
    return PrincipalHookContext(
        principal_type=PrincipalType.SYSTEM,
        principal_id="system",
        role=None,
        permissions=(),
        membership_id=None,
        source_type=None,
        source_id=None,
    )


def _default_causality(
    subject_type: str, subject_id: str,
) -> CausalityHookContext:
    return CausalityHookContext(
        correlation_id=None,
        causation_id=None,
        event_id=f"{subject_type}:{subject_id}",
        run_id=subject_id if subject_type == "agent_run" else "",
        conversation_id=None,
        session_id=None,
        message_id=None,
        reply_to=None,
        external_event_id=None,
    )


def _default_security() -> SecurityHookContext:
    now = utcnow_naive()
    return SecurityHookContext(
        attempt=0,
        deadline=None,
        created_at=now,
        available_at=now,
        policy_labels=(),
        security_labels=(),
        data_classification=__import__(
            "magi.bus.hooks.contracts", fromlist=["HookDataClassification"],
        ).HookDataClassification.INTERNAL,
    )


__all__ = [
    "EvaluationRequest",
    "HookRegistrationError",
    "HookService",
]


# Silence "imported but unused" linters.
_ = (datetime, Iterable, timezone)

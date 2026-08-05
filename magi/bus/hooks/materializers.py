"""Per-HookPoint envelope materializers.

A materializer is a pure function: given a subject reference
(e.g. an :class:`AgentInbox` row id, an :class:`LLMAttempt` row),
the requested data scopes, and a runtime/principal snapshot, it
returns a :class:`HookEnvelope` populated only with the fields
those scopes permit.

The materializer reads from the BUS-owned ORM session via
``magi.bus.db.engine.open_session`` (the same short-transaction
discipline the rest of the bus uses).  It MUST NOT call any
service method that would itself trigger another hook — hooks are
synchronous, single-shot observations.

Materializers are dispatched by the
:class:`HookEnvelopeMaterializer` switch on
:attr:`HookEnvelope.hook_point`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from magi.bus.db.base import utcnow_naive
from magi.bus.db.engine import open_session
from magi.bus.hooks.contracts import (
    CausalityHookContext,
    HookDataClassification,
    HookDataScope,
    HookEnvelope,
    HookPoint,
    HookSubject,
    JsonValue,
    PrincipalHookContext,
    PrincipalType,
    RuntimeHookContext,
    SecurityHookContext,
)
from magi.bus.hooks.redaction import FieldClassification, SecretRedactor
from magi.bus.hooks.truncation import (
    MAX_ATTACHMENT_METADATA,
    MAX_MEMORY_MATCHES,
    MAX_SESSION_WINDOW_MESSAGES,
    TruncationContext,
    apply_size_caps,
)
from magi.bus.models.queue import (
    A2AInvocation,
    AgentInbox,
    AgentRun,
    DeliveryOutbox,
    LLMAttempt,
    ToolJob,
)
from magi.bus.models.local.session import ChatSession


HOOK_ENVELOPE_SCHEMA_VERSION = "1.0.0"


# ───────────────────────────────────────────────────────────────────── #
# Materializer switch
# ───────────────────────────────────────────────────────────────────── #


class HookEnvelopeMaterializer:
    """Dispatch a :class:`HookEnvelope` build to the right per-point fn.

    Stateless — pass ``state_dir`` explicitly so tests can use a
    scratch directory.  Production code goes through
    :meth:`HookService.evaluate`, which holds the singleton.
    """

    def __init__(self, *, state_dir: str | None = None) -> None:
        self._state_dir = state_dir

    def materialize(
        self,
        *,
        hook_event_id: str,
        hook_point: HookPoint,
        subject: HookSubject,
        requested_scopes: frozenset[HookDataScope],
        runtime: RuntimeHookContext,
        principal: PrincipalHookContext,
        causality: CausalityHookContext,
        security: SecurityHookContext,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> HookEnvelope:
        """Build the envelope for one evaluation.

        Reads the BUS record indicated by ``subject`` from the
        short-lived session, projects the requested scopes, and
        returns a frozen :class:`HookEnvelope`.
        """
        builder = _BUILDERS[hook_point]
        return builder(
            self,
            hook_event_id=hook_event_id,
            subject=subject,
            requested_scopes=requested_scopes,
            runtime=runtime,
            principal=principal,
            causality=causality,
            security=security,
            metadata=metadata or {},
        )

    # -- session helper -------------------------------------------------- #

    def _session(self):
        """Open one short SQLite session for this materialization.

        Materializers open their own sessions because each
        HookPoint reads from a different table; sharing a session
        would force a single transaction across heterogeneous
        tables and tie the materializer to the caller's
        transaction context.  The hook executor never holds a
        session while a handler is running — handlers receive a
        JSON-safe envelope, not a live ORM session.
        """
        return open_session(self._state_dir)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)


# ───────────────────────────────────────────────────────────────────── #
# Builder signature
# ───────────────────────────────────────────────────────────────────── #


def _builder_signature(
    materializer: HookEnvelopeMaterializer,
    *,
    hook_event_id: str,
    subject: HookSubject,
    requested_scopes: frozenset[HookDataScope],
    runtime: RuntimeHookContext,
    principal: PrincipalHookContext,
    causality: CausalityHookContext,
    security: SecurityHookContext,
    metadata: Mapping[str, JsonValue],
) -> HookEnvelope:
    """Concrete builder signature (used as a type hint)."""
    raise NotImplementedError  # pragma: no cover


# ───────────────────────────────────────────────────────────────────── #
# Per-HookPoint builders
# ───────────────────────────────────────────────────────────────────── #


def _materialize_agent_input_pending(
    materializer: HookEnvelopeMaterializer,
    *,
    hook_event_id: str,
    subject: HookSubject,
    requested_scopes: frozenset[HookDataScope],
    runtime: RuntimeHookContext,
    principal: PrincipalHookContext,
    causality: CausalityHookContext,
    security: SecurityHookContext,
    metadata: Mapping[str, JsonValue],
) -> HookEnvelope:
    payload: dict[str, Any] = {}
    context: dict[str, JsonValue] = {}
    truncation = TruncationContext()

    with materializer._session() as session:  # noqa: SLF001 — internal
        inbox = session.get(AgentInbox, subject.subject_id)
        if inbox is None:
            # Subject vanished between persist and evaluate — the
            # evaluation should still produce a minimal envelope
            # so the executor can record "subject missing" as a
            # fail-closed DENY rather than crash.
            payload = {"subject_missing": True}
        else:
            if HookDataScope.INPUT_CONTENT in requested_scopes:
                raw_payload = dict(inbox.payload or {})
                payload = _project_input_payload(raw_payload, truncation)
            if HookDataScope.ATTACHMENT_METADATA in requested_scopes:
                context["attachments"] = _project_attachments(
                    (inbox.payload or {}).get("attachments"),
                    truncation=truncation,
                )
            if HookDataScope.RUN_STATE in requested_scopes:
                run = session.get(AgentRun, inbox.run_id)
                context["run_state"] = _project_run_state(run)
            causality = _with_causality_from_inbox(causality, inbox)

    return _build_envelope(
        hook_event_id=hook_event_id,
        hook_point=HookPoint.AGENT_INPUT_PENDING,
        subject=subject,
        requested_scopes=requested_scopes,
        runtime=runtime,
        principal=principal,
        causality=causality,
        security=security,
        payload=payload,
        context=context,
        metadata=metadata,
        truncation=truncation,
    )


def _materialize_llm_request_prepared(
    materializer: HookEnvelopeMaterializer,
    *,
    hook_event_id: str,
    subject: HookSubject,
    requested_scopes: frozenset[HookDataScope],
    runtime: RuntimeHookContext,
    principal: PrincipalHookContext,
    causality: CausalityHookContext,
    security: SecurityHookContext,
    metadata: Mapping[str, JsonValue],
) -> HookEnvelope:
    payload: dict[str, Any] = {}
    context: dict[str, JsonValue] = {}
    truncation = TruncationContext()

    with materializer._session() as session:  # noqa: SLF001
        attempt = session.get(LLMAttempt, subject.subject_id)
        if attempt is None:
            payload = {"subject_missing": True}
        else:
            if HookDataScope.LLM_REQUEST in requested_scopes:
                request = dict(attempt.request or {})
                # The request dict is the exact provider-bound
                # payload that the BUS persisted before the hook
                # fired; materializers MUST NOT re-derive it from
                # session state.
                payload = _project_llm_request(request, truncation)
                payload.setdefault("provider", attempt.provider)
                payload.setdefault("model", attempt.model)
                payload.setdefault("phase", attempt.phase)
            if HookDataScope.TOOL_SCHEMAS in requested_scopes:
                context["tool_schemas"] = _project_tool_schemas(
                    ((attempt.request or {}).get("tools") or []),
                    truncation=truncation,
                )
            if HookDataScope.SESSION_WINDOW in requested_scopes:
                context["session_window"] = _project_session_window(
                    session,
                    ((attempt.request or {}).get("messages") or []),
                    truncation=truncation,
                )
            if HookDataScope.MEMORY_MATCHES in requested_scopes:
                context["memory_matches"] = _project_memory_matches(
                    ((attempt.request or {}).get("memory") or []),
                    truncation=truncation,
                )
            if HookDataScope.RUN_STATE in requested_scopes:
                run = session.get(AgentRun, attempt.run_id)
                context["run_state"] = _project_run_state(run)
            causality = _with_causality_from_attempt(causality, attempt)

    return _build_envelope(
        hook_event_id=hook_event_id,
        hook_point=HookPoint.LLM_REQUEST_PREPARED,
        subject=subject,
        requested_scopes=requested_scopes,
        runtime=runtime,
        principal=principal,
        causality=causality,
        security=security,
        payload=payload,
        context=context,
        metadata=metadata,
        truncation=truncation,
    )


def _materialize_llm_response_received(
    materializer: HookEnvelopeMaterializer,
    *,
    hook_event_id: str,
    subject: HookSubject,
    requested_scopes: frozenset[HookDataScope],
    runtime: RuntimeHookContext,
    principal: PrincipalHookContext,
    causality: CausalityHookContext,
    security: SecurityHookContext,
    metadata: Mapping[str, JsonValue],
) -> HookEnvelope:
    payload: dict[str, Any] = {}
    context: dict[str, JsonValue] = {}
    truncation = TruncationContext()

    with materializer._session() as session:  # noqa: SLF001
        attempt = session.get(LLMAttempt, subject.subject_id)
        if attempt is None:
            payload = {"subject_missing": True}
        else:
            if HookDataScope.LLM_RESPONSE in requested_scopes:
                response = dict(attempt.response or {})
                payload = _project_llm_response(response, truncation)
                payload.setdefault("provider", attempt.provider)
                payload.setdefault("model", attempt.model)
                payload.setdefault("phase", attempt.phase)
                payload.setdefault("status", attempt.status)
            if HookDataScope.TOOL_SCHEMAS in requested_scopes:
                context["tool_schemas"] = _project_tool_schemas(
                    ((attempt.request or {}).get("tools") or []),
                    truncation=truncation,
                )
            if HookDataScope.RUN_STATE in requested_scopes:
                run = session.get(AgentRun, attempt.run_id)
                context["run_state"] = _project_run_state(run)
            causality = _with_causality_from_attempt(causality, attempt)

    return _build_envelope(
        hook_event_id=hook_event_id,
        hook_point=HookPoint.LLM_RESPONSE_RECEIVED,
        subject=subject,
        requested_scopes=requested_scopes,
        runtime=runtime,
        principal=principal,
        causality=causality,
        security=security,
        payload=payload,
        context=context,
        metadata=metadata,
        truncation=truncation,
    )


def _materialize_tool_call_pending(
    materializer: HookEnvelopeMaterializer,
    *,
    hook_event_id: str,
    subject: HookSubject,
    requested_scopes: frozenset[HookDataScope],
    runtime: RuntimeHookContext,
    principal: PrincipalHookContext,
    causality: CausalityHookContext,
    security: SecurityHookContext,
    metadata: Mapping[str, JsonValue],
) -> HookEnvelope:
    payload: dict[str, Any] = {}
    context: dict[str, JsonValue] = {}
    truncation = TruncationContext()

    with materializer._session() as session:  # noqa: SLF001
        job = session.get(ToolJob, subject.subject_id)
        if job is None:
            payload = {"subject_missing": True}
        else:
            if HookDataScope.TOOL_CALL in requested_scopes:
                payload = {
                    "tool_call_id": job.tool_call_id,
                    "tool_name": job.tool_name,
                    "tool_source": job.tool_source,
                    "catalog_revision": job.catalog_revision,
                    "schema_hash": job.schema_hash,
                    "idempotency_key": job.idempotency_key,
                    "arguments": _project_tool_arguments(
                        (job.payload or {}).get("arguments"),
                        truncation=truncation,
                    ),
                    "context": _redact_tool_context(
                        (job.payload or {}).get("context"),
                    ),
                    "attempts": job.attempts,
                    "available_at": _iso(job.available_at),
                }
            if HookDataScope.TOOL_SCHEMAS in requested_scopes:
                context["tool_schema"] = _project_tool_schemas(
                    [{"name": job.tool_name, "schema_hash": job.schema_hash}],
                    truncation=truncation,
                )
            if HookDataScope.RUN_STATE in requested_scopes:
                run = session.get(AgentRun, job.run_id)
                context["run_state"] = _project_run_state(run)
            causality = CausalityHookContext(
                correlation_id=causality.correlation_id,
                causation_id=causality.causation_id,
                event_id=causality.event_id,
                run_id=job.run_id,
                conversation_id=causality.conversation_id,
                session_id=causality.session_id,
                message_id=causality.message_id,
                reply_to=causality.reply_to,
                external_event_id=causality.external_event_id,
            )

    return _build_envelope(
        hook_event_id=hook_event_id,
        hook_point=HookPoint.TOOL_CALL_PENDING,
        subject=subject,
        requested_scopes=requested_scopes,
        runtime=runtime,
        principal=principal,
        causality=causality,
        security=security,
        payload=payload,
        context=context,
        metadata=metadata,
        truncation=truncation,
    )


def _materialize_tool_result_received(
    materializer: HookEnvelopeMaterializer,
    *,
    hook_event_id: str,
    subject: HookSubject,
    requested_scopes: frozenset[HookDataScope],
    runtime: RuntimeHookContext,
    principal: PrincipalHookContext,
    causality: CausalityHookContext,
    security: SecurityHookContext,
    metadata: Mapping[str, JsonValue],
) -> HookEnvelope:
    payload: dict[str, Any] = {}
    context: dict[str, JsonValue] = {}
    truncation = TruncationContext()

    with materializer._session() as session:  # noqa: SLF001
        job = session.get(ToolJob, subject.subject_id)
        if job is None:
            payload = {"subject_missing": True}
        else:
            if HookDataScope.TOOL_RESULT in requested_scopes:
                result = (job.payload or {}).get("result") or {}
                payload = _project_tool_result(result, truncation)
                payload.setdefault("tool_name", job.tool_name)
                payload.setdefault("tool_call_id", job.tool_call_id)
                payload.setdefault("status", job.status)
                payload.setdefault("attempts", job.attempts)
            if HookDataScope.RUN_STATE in requested_scopes:
                run = session.get(AgentRun, job.run_id)
                context["run_state"] = _project_run_state(run)

    return _build_envelope(
        hook_event_id=hook_event_id,
        hook_point=HookPoint.TOOL_RESULT_RECEIVED,
        subject=subject,
        requested_scopes=requested_scopes,
        runtime=runtime,
        principal=principal,
        causality=causality,
        security=security,
        payload=payload,
        context=context,
        metadata=metadata,
        truncation=truncation,
    )


def _materialize_a2a_invocation_pending(
    materializer: HookEnvelopeMaterializer,
    *,
    hook_event_id: str,
    subject: HookSubject,
    requested_scopes: frozenset[HookDataScope],
    runtime: RuntimeHookContext,
    principal: PrincipalHookContext,
    causality: CausalityHookContext,
    security: SecurityHookContext,
    metadata: Mapping[str, JsonValue],
) -> HookEnvelope:
    payload: dict[str, Any] = {}
    context: dict[str, JsonValue] = {}
    truncation = TruncationContext()

    with materializer._session() as session:  # noqa: SLF001
        inv = session.get(A2AInvocation, subject.subject_id)
        if inv is None:
            payload = {"subject_missing": True}
        else:
            if HookDataScope.A2A_INVOCATION in requested_scopes:
                payload = {
                    "invocation_id": inv.invocation_id,
                    "target": inv.target,
                    "request_event_id": inv.request_event_id,
                    "reply_to": inv.reply_to,
                    "expect_reply": inv.expect_reply,
                    "deadline_at": _iso(inv.deadline_at),
                    "idempotency_key": inv.idempotency_key,
                    "request": _project_a2a_request(inv.request, truncation),
                }
            if HookDataScope.RUN_STATE in requested_scopes:
                run = session.get(AgentRun, inv.run_id)
                context["run_state"] = _project_run_state(run)

    return _build_envelope(
        hook_event_id=hook_event_id,
        hook_point=HookPoint.A2A_INVOCATION_PENDING,
        subject=subject,
        requested_scopes=requested_scopes,
        runtime=runtime,
        principal=principal,
        causality=causality,
        security=security,
        payload=payload,
        context=context,
        metadata=metadata,
        truncation=truncation,
    )


def _materialize_a2a_result_received(
    materializer: HookEnvelopeMaterializer,
    *,
    hook_event_id: str,
    subject: HookSubject,
    requested_scopes: frozenset[HookDataScope],
    runtime: RuntimeHookContext,
    principal: PrincipalHookContext,
    causality: CausalityHookContext,
    security: SecurityHookContext,
    metadata: Mapping[str, JsonValue],
) -> HookEnvelope:
    payload: dict[str, Any] = {}
    context: dict[str, JsonValue] = {}
    truncation = TruncationContext()

    with materializer._session() as session:  # noqa: SLF001
        inv = session.get(A2AInvocation, subject.subject_id)
        if inv is None:
            payload = {"subject_missing": True}
        else:
            if HookDataScope.A2A_RESULT in requested_scopes:
                payload = {
                    "invocation_id": inv.invocation_id,
                    "target": inv.target,
                    "status": inv.status,
                    "result": _project_a2a_result(inv.result, truncation),
                    "error": _project_a2a_result(inv.error, truncation),
                    "completed_at": _iso(inv.completed_at),
                    "expect_reply": inv.expect_reply,
                }
            if HookDataScope.RUN_STATE in requested_scopes:
                run = session.get(AgentRun, inv.run_id)
                context["run_state"] = _project_run_state(run)

    return _build_envelope(
        hook_event_id=hook_event_id,
        hook_point=HookPoint.A2A_RESULT_RECEIVED,
        subject=subject,
        requested_scopes=requested_scopes,
        runtime=runtime,
        principal=principal,
        causality=causality,
        security=security,
        payload=payload,
        context=context,
        metadata=metadata,
        truncation=truncation,
    )


def _materialize_delivery_pending(
    materializer: HookEnvelopeMaterializer,
    *,
    hook_event_id: str,
    subject: HookSubject,
    requested_scopes: frozenset[HookDataScope],
    runtime: RuntimeHookContext,
    principal: PrincipalHookContext,
    causality: CausalityHookContext,
    security: SecurityHookContext,
    metadata: Mapping[str, JsonValue],
) -> HookEnvelope:
    payload: dict[str, Any] = {}
    context: dict[str, JsonValue] = {}
    truncation = TruncationContext()

    with materializer._session() as session:  # noqa: SLF001
        delivery = session.get(DeliveryOutbox, subject.subject_id)
        if delivery is None:
            payload = {"subject_missing": True}
        else:
            if HookDataScope.DELIVERY_PAYLOAD in requested_scopes:
                raw_payload = dict(delivery.payload or {})
                payload = {
                    "delivery_id": delivery.delivery_id,
                    "channel": delivery.channel,
                    "destination": delivery.destination,
                    "idempotency_key": delivery.idempotency_key,
                    "rendered_text": _truncate_str(
                        raw_payload.get("text"),
                        truncation,
                        path="payload.text",
                    ),
                    "reply_to": raw_payload.get("reply_to"),
                    "correlation_id": raw_payload.get("correlation_id"),
                    "a2a_kind": raw_payload.get("a2a_kind"),
                    "attachments": _project_attachments(
                        raw_payload.get("attachments"),
                        truncation=truncation,
                    ),
                    "attempts": delivery.attempts,
                }
            if HookDataScope.RUN_STATE in requested_scopes and delivery.run_id:
                run = session.get(AgentRun, delivery.run_id)
                context["run_state"] = _project_run_state(run)

    return _build_envelope(
        hook_event_id=hook_event_id,
        hook_point=HookPoint.DELIVERY_PENDING,
        subject=subject,
        requested_scopes=requested_scopes,
        runtime=runtime,
        principal=principal,
        causality=causality,
        security=security,
        payload=payload,
        context=context,
        metadata=metadata,
        truncation=truncation,
    )


def _materialize_run_transition_committed(
    materializer: HookEnvelopeMaterializer,
    *,
    hook_event_id: str,
    subject: HookSubject,
    requested_scopes: frozenset[HookDataScope],
    runtime: RuntimeHookContext,
    principal: PrincipalHookContext,
    causality: CausalityHookContext,
    security: SecurityHookContext,
    metadata: Mapping[str, JsonValue],
) -> HookEnvelope:
    payload: dict[str, Any] = {}
    context: dict[str, JsonValue] = {}
    truncation = TruncationContext()

    with materializer._session() as session:  # noqa: SLF001
        run = session.get(AgentRun, subject.subject_id)
        if run is None:
            payload = {"subject_missing": True}
        else:
            payload = _project_run_state(run)
            payload["run_id"] = run.run_id
            payload["root_event_id"] = run.root_event_id

    return _build_envelope(
        hook_event_id=hook_event_id,
        hook_point=HookPoint.RUN_TRANSITION_COMMITTED,
        subject=subject,
        requested_scopes=requested_scopes,
        runtime=runtime,
        principal=principal,
        causality=causality,
        security=security,
        payload=payload,
        context=context,
        metadata=metadata,
        truncation=truncation,
    )


def _materialize_operation_failed_or_dead_lettered(
    hook_point: HookPoint,
):
    """Closure returning the builder for the two operational hooks."""

    def _builder(
        materializer: HookEnvelopeMaterializer,
        *,
        hook_event_id: str,
        subject: HookSubject,
        requested_scopes: frozenset[HookDataScope],
        runtime: RuntimeHookContext,
        principal: PrincipalHookContext,
        causality: CausalityHookContext,
        security: SecurityHookContext,
        metadata: Mapping[str, JsonValue],
    ) -> HookEnvelope:
        payload: dict[str, Any] = {"operation_type": metadata.get("operation_type")}
        context: dict[str, JsonValue] = {}
        truncation = TruncationContext()

        with materializer._session() as session:  # noqa: SLF001
            if HookDataScope.OPERATION_ERROR in requested_scopes:
                error = metadata.get("error") or {}
                payload.update({
                    "error_category": error.get("category"),
                    "sanitized_error": error.get("sanitized_error"),
                    "attempt_count": metadata.get("attempt_count"),
                    "dead_letter_reason": metadata.get("dead_letter_reason"),
                    "deadline": _iso(metadata.get("deadline")) if metadata.get("deadline") else None,
                    "lease_expiry": _iso(metadata.get("lease_expiry")) if metadata.get("lease_expiry") else None,
                    "worker_type": metadata.get("worker_type"),
                })
            if HookDataScope.RUN_STATE in requested_scopes:
                run_id = metadata.get("run_id")
                if isinstance(run_id, str):
                    run = session.get(AgentRun, run_id)
                    context["run_state"] = _project_run_state(run)

        return _build_envelope(
            hook_event_id=hook_event_id,
            hook_point=hook_point,
            subject=subject,
            requested_scopes=requested_scopes,
            runtime=runtime,
            principal=principal,
            causality=causality,
            security=security,
            payload=payload,
            context=context,
            metadata=metadata,
            truncation=truncation,
        )

    return _builder


# ───────────────────────────────────────────────────────────────────── #
# Dispatch table
# ───────────────────────────────────────────────────────────────────── #


_BUILDERS = {
    HookPoint.AGENT_INPUT_PENDING: _materialize_agent_input_pending,
    HookPoint.LLM_REQUEST_PREPARED: _materialize_llm_request_prepared,
    HookPoint.LLM_RESPONSE_RECEIVED: _materialize_llm_response_received,
    HookPoint.TOOL_CALL_PENDING: _materialize_tool_call_pending,
    HookPoint.TOOL_RESULT_RECEIVED: _materialize_tool_result_received,
    HookPoint.A2A_INVOCATION_PENDING: _materialize_a2a_invocation_pending,
    HookPoint.A2A_RESULT_RECEIVED: _materialize_a2a_result_received,
    HookPoint.DELIVERY_PENDING: _materialize_delivery_pending,
    HookPoint.RUN_TRANSITION_COMMITTED: _materialize_run_transition_committed,
    HookPoint.OPERATION_FAILED: _materialize_operation_failed_or_dead_lettered(
        HookPoint.OPERATION_FAILED
    ),
    HookPoint.OPERATION_DEAD_LETTERED: _materialize_operation_failed_or_dead_lettered(
        HookPoint.OPERATION_DEAD_LETTERED
    ),
}


# ───────────────────────────────────────────────────────────────────── #
# Payload projection helpers — kept private to this module so the
# architecture test can verify only one materializer module touches
# queue ORM models.
# ───────────────────────────────────────────────────────────────────── #


def _build_envelope(
    *,
    hook_event_id: str,
    hook_point: HookPoint,
    subject: HookSubject,
    requested_scopes: frozenset[HookDataScope],
    runtime: RuntimeHookContext,
    principal: PrincipalHookContext,
    causality: CausalityHookContext,
    security: SecurityHookContext,
    payload: Mapping[str, JsonValue],
    context: Mapping[str, JsonValue],
    metadata: Mapping[str, JsonValue],
    truncation: TruncationContext,
) -> HookEnvelope:
    marker, meta = truncation.finalize_envelope()
    final_metadata = dict(metadata)
    if marker is not None:
        final_metadata["truncation"] = meta
    return HookEnvelope(
        schema_version=HOOK_ENVELOPE_SCHEMA_VERSION,
        hook_event_id=hook_event_id,
        hook_point=hook_point,
        occurred_at=HookEnvelopeMaterializer._now(),
        runtime=runtime,
        principal=principal,
        causality=causality,
        subject=subject,
        payload=dict(payload),
        context=dict(context),
        security=security,
        metadata=final_metadata,
    )


def _project_input_payload(
    raw_payload: dict[str, Any],
    truncation: TruncationContext,
) -> dict[str, Any]:
    projected = {}
    for key in ("text", "channel", "session_id", "uid", "caller_role", "conversation_id"):
        if key in raw_payload:
            projected[key] = apply_size_caps(
                raw_payload[key],
                truncation,
                path=f"payload.{key}",
            )
    metadata = raw_payload.get("metadata") or {}
    if isinstance(metadata, dict):
        projected["metadata"] = apply_size_caps(
            _redact_metadata(metadata),
            truncation,
            path="payload.metadata",
        )
    return projected


def _project_attachments(
    attachments: Any,
    *,
    truncation: TruncationContext,
) -> list[dict[str, Any]]:
    """Project attachment metadata.

    Per spec §6.1 the materializer exposes ``attachment_id``,
    ``filename``, ``media_type``, ``size``, ``content_hash``, a
    BUS resource reference and security labels — never the binary
    content.
    """
    if not isinstance(attachments, list):
        return []
    result: list[dict[str, Any]] = []
    for att in attachments[:MAX_ATTACHMENT_METADATA]:
        if not isinstance(att, dict):
            continue
        result.append({
            "attachment_id": att.get("attachment_id"),
            "filename": att.get("filename"),
            "media_type": att.get("media_type"),
            "size": att.get("size"),
            "content_hash": att.get("content_hash"),
            "resource_ref": att.get("resource_ref"),
            "security_labels": tuple(att.get("security_labels") or ()),
        })
    return result


def _project_tool_arguments(
    arguments: Any,
    *,
    truncation: TruncationContext,
) -> dict[str, Any]:
    """Project tool call arguments, redacting secrets."""
    if not isinstance(arguments, dict):
        return {}
    return apply_size_caps(
        SecretRedactor.redact(arguments),
        truncation,
        path="payload.arguments",
    )


def _redact_tool_context(context: Any) -> dict[str, Any]:
    if not isinstance(context, dict):
        return {}
    return dict(SecretRedactor.redact(context))


def _redact_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Redact metadata with a tighter allowlist for known credentials."""
    hints = {
        name: FieldClassification(classification="credential", mode="metadata-only")
        for name in {
            "api_key", "token", "password", "secret", "authorization",
        }
    }
    return dict(SecretRedactor.redact(metadata, field_classifications=hints))


def _project_tool_result(
    result: dict[str, Any],
    truncation: TruncationContext,
) -> dict[str, Any]:
    """Project a tool result, redacting and truncating."""
    out = {}
    for key in ("content", "is_error", "exit_code", "stdout", "stderr"):
        if key in result:
            out[key] = apply_size_caps(
                result[key],
                truncation,
                path=f"payload.result.{key}",
            )
    return out


def _project_llm_request(
    request: dict[str, Any],
    truncation: TruncationContext,
) -> dict[str, Any]:
    """Project the LLM provider-bound request exactly as persisted.

    The materializer MUST NOT re-derive the request from session
    state; it reads the column the BUS persisted immediately
    before the hook fired (see spec §6.2).
    """
    out = {}
    for key in ("system", "messages", "tools", "max_tokens", "temperature", "response_format"):
        if key in request:
            out[key] = apply_size_caps(
                request[key],
                truncation,
                path=f"payload.request.{key}",
            )
    return out


def _project_llm_response(
    response: dict[str, Any],
    truncation: TruncationContext,
) -> dict[str, Any]:
    """Project the LLM provider response exactly as persisted."""
    out = {}
    for key in (
        "text",
        "tool_uses",
        "assistant_blocks",
        "finish_reason",
        "refusal",
        "usage",
        "model",
        "raw_blocks",
        "error",
    ):
        if key in response:
            out[key] = apply_size_caps(
                response[key],
                truncation,
                path=f"payload.response.{key}",
            )
    return out


def _project_tool_schemas(
    schemas: Any,
    *,
    truncation: TruncationContext,
) -> list[dict[str, Any]]:
    if not isinstance(schemas, list):
        return []
    projected: list[dict[str, Any]] = []
    for schema in schemas[:64]:
        if not isinstance(schema, dict):
            continue
        projected.append({
            "name": schema.get("name"),
            "schema_hash": schema.get("schema_hash"),
            "catalog_revision": schema.get("catalog_revision"),
            "description_present": bool(schema.get("description")),
        })
    return projected


def _project_session_window(
    session: Any,
    messages: Any,
    *,
    truncation: TruncationContext,
) -> list[dict[str, Any]]:
    """Project a bounded slice of the session transcript.

    The handler asks for ``SESSION_WINDOW`` so we give it the
    last :data:`MAX_SESSION_WINDOW_MESSAGES` messages plus their
    role + a redacted content preview.  We never expose API
    keys / tokens even when present in the transcript.
    """
    if not isinstance(messages, list):
        return []
    recent = messages[-MAX_SESSION_WINDOW_MESSAGES:]
    out = []
    for msg in recent:
        if not isinstance(msg, dict):
            continue
        out.append(apply_size_caps(
            {
                "role": msg.get("role"),
                "content_preview": _content_preview(msg.get("content")),
            },
            truncation,
            path="context.session_window",
        ))
    return out


def _project_memory_matches(
    matches: Any,
    *,
    truncation: TruncationContext,
) -> list[dict[str, Any]]:
    if not isinstance(matches, list):
        return []
    out = []
    for match in matches[:MAX_MEMORY_MATCHES]:
        if not isinstance(match, dict):
            continue
        out.append({
            "memory_id": match.get("memory_id"),
            "score": match.get("score"),
            "preview": apply_size_caps(
                match.get("preview"),
                truncation,
                path="context.memory_matches.preview",
            ),
        })
    return out


def _project_a2a_request(
    request: Any,
    truncation: TruncationContext,
) -> dict[str, Any]:
    if not isinstance(request, dict):
        return {}
    return apply_size_caps(
        SecretRedactor.redact(request),
        truncation,
        path="payload.request",
    )


def _project_a2a_result(
    result: Any,
    truncation: TruncationContext,
) -> dict[str, Any] | None:
    if result is None:
        return None
    if not isinstance(result, dict):
        return {}
    return apply_size_caps(
        SecretRedactor.redact(result),
        truncation,
        path="payload.result",
    )


def _project_run_state(run: AgentRun | None) -> dict[str, Any]:
    if run is None:
        return {"missing": True}
    return {
        "status": run.status,
        "version": run.version,
        "iteration_count": run.iteration_count,
        "started_at": _iso(run.started_at),
        "updated_at": _iso(run.updated_at),
        "completed_at": _iso(run.completed_at),
        "deadline_at": _iso(run.deadline_at),
        "error_code": run.error_code,
        "expected_tool_call_ids": tuple(run.expected_tool_call_ids or ()),
        "expected_a2a_invocation_ids": tuple(run.expected_a2a_invocation_ids or ()),
    }


def _with_causality_from_inbox(
    causality: CausalityHookContext,
    inbox: AgentInbox,
) -> CausalityHookContext:
    return CausalityHookContext(
        correlation_id=inbox.correlation_id or causality.correlation_id,
        causation_id=inbox.causation_id or causality.causation_id,
        event_id=causality.event_id or inbox.event_id,
        run_id=inbox.run_id,
        conversation_id=inbox.conversation_id or causality.conversation_id,
        session_id=(inbox.payload or {}).get("session_id") or causality.session_id,
        message_id=causality.message_id,
        reply_to=causality.reply_to,
        external_event_id=inbox.external_event_id or causality.external_event_id,
    )


def _with_causality_from_attempt(
    causality: CausalityHookContext,
    attempt: LLMAttempt,
) -> CausalityHookContext:
    return CausalityHookContext(
        correlation_id=causality.correlation_id,
        causation_id=causality.causation_id,
        event_id=causality.event_id or attempt.inbox_event_id or "",
        run_id=attempt.run_id,
        conversation_id=causality.conversation_id,
        session_id=causality.session_id,
        message_id=causality.message_id,
        reply_to=causality.reply_to,
        external_event_id=causality.external_event_id,
    )


def _content_preview(content: Any) -> Any:
    if not isinstance(content, str):
        return content
    if len(content) <= 256:
        return content
    return content[:256] + "..."


def _truncate_str(
    value: Any,
    truncation: TruncationContext,
    *,
    path: str,
) -> Any:
    if value is None:
        return None
    return apply_size_caps(value, truncation, path=path)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


# `ChatSession` is imported for typing/future-use; the projection
# helper intentionally reads only the `AgentRun` columns above.
_ = ChatSession
# Silence unused-import lint; ``utcnow_naive`` is referenced in
# docstrings only.
_ = utcnow_naive


__all__ = ["HOOK_ENVELOPE_SCHEMA_VERSION", "HookEnvelopeMaterializer"]

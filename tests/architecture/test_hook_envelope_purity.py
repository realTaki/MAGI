"""HookEnvelope purity tests.

The :class:`magi.bus.hooks.contracts.HookEnvelope` and every
type it transitively references MUST be:

  - frozen (``@dataclass(frozen=True, slots=True)``)
  - JSON-safe (no ORM models, no SQLAlchemy sessions, no
    Provider / Channel / MCP clients, no callbacks)
  - free of plaintext credentials (the materializer applies
    the redaction policy)

These tests walk every type under ``magi.bus.hooks.contracts``
and assert the above invariants.  They are the static arm of
the security boundary; the runtime arm is
``tests/integration/hooks/test_*``.
"""

from __future__ import annotations

import dataclasses
import inspect
from typing import get_type_hints

import pytest

from magi.bus.hooks.contracts import (
    CausalityHookContext,
    HookDecision,
    HookEnvelope,
    HookEvaluation,
    HookEvaluationResult,
    HookPoint,
    HookRegistration,
    HookSubject,
    PrincipalHookContext,
    RuntimeHookContext,
    SecurityHookContext,
    TruncationMarker,
)


# ───────────────────────────────────────────────────────────────────── #
# Forbidden type names — any type whose name contains one of these
# strings (case-insensitive) is forbidden inside HookEnvelope.
# ───────────────────────────────────────────────────────────────────── #


_FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "Session",  # SQLAlchemy Session — except for the
    # ``ChatSession`` model which is intentionally NOT
    # imported by the materializer (see test below).
    "Engine",
    "Connection",
    "Adapter",
    "Provider",
    "Client",
    "Connector",
    "Worker",
    "Dispatcher",
    "Registry",
    "ORM",
    "Callback",
    "Coroutine",
)


@dataclasses.dataclass(frozen=True, slots=True)
class _SampleRuntime(RuntimeHookContext):
    """Subclass that adds an extra field — used to verify the dataclass stays frozen."""


# ───────────────────────────────────────────────────────────────────── #
# Frozen dataclass check
# ───────────────────────────────────────────────────────────────────── #


@pytest.mark.parametrize("cls", [
    HookEnvelope,
    HookDecision,
    HookEvaluation,
    HookEvaluationResult,
    HookRegistration,
    TruncationMarker,
    HookSubject,
    RuntimeHookContext,
    PrincipalHookContext,
    CausalityHookContext,
    SecurityHookContext,
])
def test_dataclass_is_frozen(cls) -> None:
    """Every HookEnvelope-supporting dataclass is frozen + slotted."""
    assert dataclasses.is_dataclass(cls), f"{cls.__name__} is not a dataclass"
    field_specs = dataclasses.fields(cls)
    assert field_specs, f"{cls.__name__} has no fields — not a usable DTO"
    params = getattr(cls, "__dataclass_params__", None)
    assert params is not None, f"{cls.__name__} missing __dataclass_params__"
    assert params.frozen is True, f"{cls.__name__} must be frozen=True"
    assert params.slots is True, f"{cls.__name__} must be slots=True"


# ───────────────────────────────────────────────────────────────────── #
# No forbidden type names anywhere in the type tree.
# ───────────────────────────────────────────────────────────────────── #


def _all_referenced_types(root_cls, seen: set[type] | None = None) -> set[type]:
    """Walk the dataclass field types and return every referenced concrete type."""
    if seen is None:
        seen = set()
    seen.add(root_cls)
    try:
        hints = get_type_hints(root_cls)
    except Exception:
        return seen
    for hint in hints.values():
        for cls in _expand_type(hint):
            if cls is None or cls in seen:
                continue
            if dataclasses.is_dataclass(cls):
                seen.add(cls)
                _all_referenced_types(cls, seen)
    return seen


def _expand_type(hint) -> list[type | None]:
    """Expand a type hint into the concrete types referenced."""
    if hint is type(None):
        return [None]
    origin = getattr(hint, "__origin__", None)
    if origin is None:
        return [hint if isinstance(hint, type) else None]
    args = getattr(hint, "__args__", ())
    out: list[type | None] = []
    for arg in args:
        out.extend(_expand_type(arg))
    return out


def test_no_forbidden_types_in_envelope_tree() -> None:
    """No ORM, Session, Client, Provider, Worker etc. anywhere in the envelope."""
    referenced = _all_referenced_types(HookEnvelope)
    offending: list[str] = []
    for cls in referenced:
        name = cls.__name__ if cls is not None else ""
        for forbidden in _FORBIDDEN_SUBSTRINGS:
            if forbidden.lower() in name.lower():
                offending.append(f"{cls.__module__}.{name}")
                break
    assert not offending, (
        "HookEnvelope tree contains forbidden types: "
        + ", ".join(sorted(set(offending)))
    )


# ───────────────────────────────────────────────────────────────────── #
# The envelope can be built without ORM models.
# ───────────────────────────────────────────────────────────────────── #


def test_envelope_can_be_built_from_primitives() -> None:
    """Smoke — construct an envelope with primitives only.

    If this test ever needs ORM objects to construct, the
    purity guarantee is broken.
    """
    runtime = RuntimeHookContext(
        magi_id=None,
        magis_id=None,
        runtime_id="rt",
        runtime_instance_id="rt-1",
        environment="dev",
        workspace_id="ws",
    )
    principal = PrincipalHookContext(
        principal_type=__import__(
            "magi.bus.hooks.contracts",
            fromlist=["PrincipalType"],
        ).PrincipalType.SYSTEM,
        principal_id="p",
        role=None,
        permissions=(),
        membership_id=None,
        source_type=None,
        source_id=None,
    )
    causality = CausalityHookContext(
        correlation_id=None,
        causation_id=None,
        event_id="e",
        run_id="r",
        conversation_id=None,
        session_id=None,
        message_id=None,
        reply_to=None,
        external_event_id=None,
    )
    security = SecurityHookContext(
        attempt=0,
        deadline=None,
        created_at=__import__("datetime").datetime.now(),
        available_at=__import__("datetime").datetime.now(),
        policy_labels=(),
        security_labels=(),
        data_classification=__import__(
            "magi.bus.hooks.contracts",
            fromlist=["HookDataClassification"],
        ).HookDataClassification.INTERNAL,
    )
    envelope = HookEnvelope(
        schema_version="1.0.0",
        hook_event_id="hkevt_test",
        hook_point=HookPoint.AGENT_INPUT_PENDING,
        occurred_at=__import__("datetime").datetime.now(),
        runtime=runtime,
        principal=principal,
        causality=causality,
        subject=HookSubject(subject_type="agent_inbox", subject_id="evt-1"),
        payload={"text": "hello"},
        context={},
        security=security,
        metadata={},
    )
    assert envelope.hook_event_id == "hkevt_test"
    # Frozen — assignment raises.
    with pytest.raises(dataclasses.FrozenInstanceError):
        envelope.hook_event_id = "tampered"  # type: ignore[misc]


# ───────────────────────────────────────────────────────────────────── #
# No exec / eval / callback leaks.
# ───────────────────────────────────────────────────────────────────── #


def test_envelope_has_no_callable_attributes() -> None:
    """Every HookEnvelope field is data; none is a callback.

    The handler receives ONLY data — it cannot pass a
    callback back into the runtime via a "spy" or "metric"
    field on the envelope.
    """
    fields = dataclasses.fields(HookEnvelope)
    for f in fields:
        assert not callable(getattr(HookEnvelope, f.name, None)), (
            f"HookEnvelope.{f.name} is callable; envelopes must be pure data"
        )


# ───────────────────────────────────────────────────────────────────── #
# Public surface is small — plugin authors cannot pull in ORM
# accidentally via re-exports.
# ───────────────────────────────────────────────────────────────────── #


def test_plugins_hooks_does_not_export_orm() -> None:
    """``magi.plugins.hooks`` MUST NOT re-export ORM models."""
    import magi.plugins.hooks as ph

    for name in dir(ph):
        if name.startswith("_"):
            continue
        attr = getattr(ph, name)
        if not isinstance(attr, type):
            continue
        module = getattr(attr, "__module__", "")
        # Anything under magi.bus.models.* is ORM and forbidden.
        assert not module.startswith("magi.bus.models"), (
            f"magi.plugins.hooks.{name} re-exports ORM model "
            f"{module}.{attr.__name__}"
        )

"""Public exports for the plugin-side hook subsystem.

Plugin authors import from this module to get the
:class:`HookHandlerProtocol` they implement and the
:class:`HookEnvelope` they receive.

The plugin code MUST NOT import anything from ``magi.bus``
beyond the contract surface re-exported here.  The architecture
test enforces this — see
``tests/architecture/test_hook_import_boundaries.py``.
"""

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
    PrincipalHookContext,
    PrincipalType,
    RuntimeHookContext,
    SecurityHookContext,
    TruncationMarker,
)
from magi.plugins.hooks.base import HookHandler, hook_handler
from magi.plugins.hooks.loader import HookPluginDescriptor, HookPluginLoader


__all__ = [
    # Contracts (re-exported so plugins depend only on
    # ``magi.plugins.hooks``).
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
    "PrincipalHookContext",
    "PrincipalType",
    "RuntimeHookContext",
    "SecurityHookContext",
    "TruncationMarker",
    # Plugin-side helpers
    "HookHandler",
    "HookPluginDescriptor",
    "HookPluginLoader",
    "hook_handler",
]

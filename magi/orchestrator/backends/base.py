"""``RuntimeBackend`` Protocol — platform-neutral deployment contract.

Defines the operations every backend (Kubernetes, Local Process, …)
must implement.  See plan §4.2.

The Protocol shape is what
:class:`~magi.bus.services.runtime.BackendDispatcherService` consumes;
K8s-specific fields live in
:class:`~magi.bus.protocols.lifecycle.KubernetesBackendDetail` and are
populated only by the K8s adapter.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from magi.bus.protocols.lifecycle import (
    MagisProvisionResult,
    RuntimeOperationResult,
    RuntimeSpec,
)


@runtime_checkable
class RuntimeBackend(Protocol):
    """Protocol every concrete backend implements."""

    @property
    def kind(self) -> str:
        """Stable identifier — matches ``BackendKind`` literal."""
        ...

    def provision_magis(
        self,
        magis_id: int,
        magis_name: str,
    ) -> MagisProvisionResult:
        """Provision one MAGIS's public database + workspace."""
        ...

    def start(self, spec: RuntimeSpec) -> RuntimeOperationResult:
        """Bring one runtime up; idempotent."""
        ...

    def stop(self, spec: RuntimeSpec) -> RuntimeOperationResult:
        """Stop one runtime; state / data preserved."""
        ...

    def delete(self, spec: RuntimeSpec) -> RuntimeOperationResult:
        """Remove one runtime's deployment resources."""
        ...

    def endpoint_for(self, spec: RuntimeSpec) -> RuntimeOperationResult:
        """Return the current observed endpoint without changing state."""
        ...


__all__ = ["RuntimeBackend"]
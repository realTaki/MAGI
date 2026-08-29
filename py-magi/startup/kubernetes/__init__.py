"""Kubernetes resource management and orchestrator."""

from startup.kubernetes.client import (
    OrchestratorUnavailable,
    provision_magis,
    request_lifecycle,
)
from startup.kubernetes.contracts import (
    EvaOperationResult,
    EvaSpec,
    MagisBinding,
    MagisProvisionResult,
    MagisRuntimeConfiguration,
)
from startup.kubernetes.resources import (
    KubernetesEvaBackend,
    create_magi_resources,
    create_magis_resources,
    delete_webui_resources,
    ensure_webui_deployment,
    ensure_webui_service,
)
from startup.kubernetes.service import _verify_request, create_app

__all__ = [
    # resources
    "KubernetesEvaBackend",
    "create_magi_resources",
    "create_magis_resources",
    "ensure_webui_deployment",
    "ensure_webui_service",
    "delete_webui_resources",
    # contracts
    "EvaOperationResult",
    "EvaSpec",
    "MagisBinding",
    "MagisProvisionResult",
    "MagisRuntimeConfiguration",
    # client
    "OrchestratorUnavailable",
    "provision_magis",
    "request_lifecycle",
    # service
    "create_app",
    "_verify_request",
]

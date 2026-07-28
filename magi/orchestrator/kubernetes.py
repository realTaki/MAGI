"""Minimal Kubernetes API adapter used only by the orchestrator service.

It intentionally supports the narrow resource vocabulary required for an EVE:
Secret, PVC, and Deployment.  There is no generic manifest endpoint and no
pod exec capability.
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import Any

import httpx

from magi.orchestrator.contracts import EveOperationResult, EveSpec

_TOKEN_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
_CA_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")


def _resource_name(spec: EveSpec) -> str:
    raw = (spec.name or "eve").lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", raw).strip("-") or "eve"
    return f"magi-eve-{spec.magi_id}-{slug}"[:63].rstrip("-")


def _secret_data(values: dict[str, str]) -> dict[str, str]:
    """Render binary-safe Secret data for server-side apply.

    Kubernetes documents ``stringData`` as incompatible with server-side
    apply field ownership. Encoding here makes repeated provider rotations
    deterministic and idempotent.
    """
    return {key: base64.b64encode(value.encode()).decode() for key, value in values.items()}


class KubernetesEveBackend:
    """Apply the fixed EVE resource template to one configured namespace."""

    def __init__(self) -> None:
        host = os.environ.get("KUBERNETES_SERVICE_HOST")
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        self.base_url = os.environ.get("MAGI_K8S_API_URL") or (
            f"https://{host}:{port}" if host else ""
        )
        self.namespace = os.environ.get("MAGI_K8S_NAMESPACE", "magi")
        self.image = os.environ.get("MAGI_EVE_IMAGE", "magi:0.1.0")
        if not self.base_url or not _TOKEN_PATH.is_file():
            raise RuntimeError("Kubernetes service-account credentials are unavailable")
        self.token = _TOKEN_PATH.read_text().strip()
        self.verify: bool | str = str(_CA_PATH) if _CA_PATH.is_file() else True

    def _request(
        self, method: str, path: str, *, body: dict[str, Any] | None = None, content_type: str
    ) -> dict[str, Any]:
        with httpx.Client(verify=self.verify, timeout=20.0) as client:
            response = client.request(
                method,
                f"{self.base_url}{path}",
                json=body,
                headers={
                    "authorization": f"Bearer {self.token}",
                    "content-type": content_type,
                    "accept": "application/json",
                },
            )
        if response.status_code >= 300:
            raise RuntimeError(
                f"Kubernetes {method} {path}: {response.status_code} {response.text[:500]}"
            )
        return response.json() if response.content else {}

    def _apply(self, path: str, manifest: dict[str, Any]) -> None:
        self._request(
            "PATCH",
            f"{path}?fieldManager=magi-orchestrator&force=true",
            body=manifest,
            content_type="application/apply-patch+yaml",
        )

    def _delete(self, path: str) -> None:
        with httpx.Client(verify=self.verify, timeout=20.0) as client:
            response = client.delete(
                f"{self.base_url}{path}",
                headers={"authorization": f"Bearer {self.token}", "accept": "application/json"},
            )
        if response.status_code not in {200, 202, 404}:
            raise RuntimeError(
                f"Kubernetes DELETE {path}: {response.status_code} {response.text[:500]}"
            )

    def start(self, spec: EveSpec) -> EveOperationResult:
        if not spec.provider or not spec.api_key:
            raise ValueError("starting an EVE requires provider and API key")
        name = _resource_name(spec)
        secret_name = f"{name}-provider"
        pvc_name = f"{name}-workspace"
        labels = {
            "app.kubernetes.io/name": "magi",
            "app.kubernetes.io/component": "eve",
            "magi.io/managed-by": "magi-orchestrator",
            "magi.io/magi-id": str(spec.magi_id),
            "magi.io/magic-id": str(spec.magic_id),
        }
        prefix = f"/api/v1/namespaces/{self.namespace}"
        self._apply(
            f"{prefix}/secrets/{secret_name}",
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": secret_name, "labels": labels},
                "type": "Opaque",
                "data": _secret_data(
                    {
                        "MAGI_LLM_PROVIDER": spec.provider,
                        "MAGI_LLM_API_KEY": spec.api_key,
                        **({"MAGI_LLM_MODEL": spec.model} if spec.model else {}),
                    }
                ),
            },
        )
        self._apply(
            f"{prefix}/persistentvolumeclaims/{pvc_name}",
            {
                "apiVersion": "v1",
                "kind": "PersistentVolumeClaim",
                "metadata": {"name": pvc_name, "labels": labels},
                "spec": {
                    "accessModes": ["ReadWriteOnce"],
                    "resources": {"requests": {"storage": "10Gi"}},
                },
            },
        )
        self._apply(
            f"/apis/apps/v1/namespaces/{self.namespace}/deployments/{name}",
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": name, "labels": labels},
                "spec": {
                    "replicas": 1,
                    "strategy": {"type": "Recreate"},
                    "selector": {"matchLabels": {"magi.io/magi-id": str(spec.magi_id)}},
                    "template": {
                        "metadata": {"labels": labels},
                        "spec": {
                            "terminationGracePeriodSeconds": 30,
                            "securityContext": {"runAsNonRoot": True, "fsGroup": 1000},
                            "containers": [
                                {
                                    "name": "magi",
                                    "image": self.image,
                                    "imagePullPolicy": "IfNotPresent",
                                    "env": [
                                        {"name": "MAGI_NODE_ROLE", "value": "eve"},
                                        {"name": "MAGI_RUNTIME_ID", "value": str(spec.magi_id)},
                                        {
                                            "name": "MAGI_LLM_PROVIDER",
                                            "valueFrom": {
                                                "secretKeyRef": {
                                                    "name": secret_name,
                                                    "key": "MAGI_LLM_PROVIDER",
                                                }
                                            },
                                        },
                                        {
                                            "name": "MAGI_LLM_API_KEY",
                                            "valueFrom": {
                                                "secretKeyRef": {
                                                    "name": secret_name,
                                                    "key": "MAGI_LLM_API_KEY",
                                                }
                                            },
                                        },
                                    ],
                                    "volumeMounts": [
                                        {"name": "workspace", "mountPath": "/workspace"}
                                    ],
                                    "securityContext": {
                                        "allowPrivilegeEscalation": False,
                                        "capabilities": {"drop": ["ALL"]},
                                    },
                                }
                            ],
                            "volumes": [
                                {
                                    "name": "workspace",
                                    "persistentVolumeClaim": {"claimName": pvc_name},
                                }
                            ],
                        },
                    },
                },
            },
        )
        return EveOperationResult(
            observed_state="provisioning",
            namespace=self.namespace,
            deployment_name=name,
            workspace_claim_name=pvc_name,
            credential_secret_name=secret_name,
            message="EVE Deployment applied; wait for its Pod to become Ready.",
        )

    def stop(self, spec: EveSpec) -> EveOperationResult:
        name = _resource_name(spec)
        self._request(
            "PATCH",
            f"/apis/apps/v1/namespaces/{self.namespace}/deployments/{name}",
            body={"spec": {"replicas": 0}},
            content_type="application/merge-patch+json",
        )
        return EveOperationResult(
            observed_state="stopped",
            namespace=self.namespace,
            deployment_name=name,
            workspace_claim_name=f"{name}-workspace",
            credential_secret_name=f"{name}-provider",
            message="EVE scaled to zero; its workspace and provider secret were retained.",
        )

    def delete(self, spec: EveSpec) -> EveOperationResult:
        """Remove the managed resource set after an explicit Admin delete."""
        name = _resource_name(spec)
        prefix = f"/api/v1/namespaces/{self.namespace}"
        self._delete(f"/apis/apps/v1/namespaces/{self.namespace}/deployments/{name}")
        self._delete(f"{prefix}/persistentvolumeclaims/{name}-workspace")
        self._delete(f"{prefix}/secrets/{name}-provider")
        return EveOperationResult(
            observed_state="deleted",
            namespace=self.namespace,
            deployment_name=name,
            workspace_claim_name=f"{name}-workspace",
            credential_secret_name=f"{name}-provider",
            message="EVE Deployment, workspace PVC, and provider Secret were deleted.",
        )

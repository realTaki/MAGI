"""Minimal Kubernetes API adapter used only by the orchestrator service.

It intentionally supports the narrow resource vocabulary required for an EVA:
Secret, PVC, and Deployment.  There is no generic manifest endpoint and no
pod exec capability.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from magi.orchestrator.contracts import EvaOperationResult, EvaSpec, MagisBinding, MagisProvisionResult

_TOKEN_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
_CA_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")


def _resource_name(spec: EvaSpec) -> str:
    raw = (spec.name or "eva").lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", raw).strip("-") or "eva"
    return f"magi-eva-{spec.magic_id}-{slug}"[:63].rstrip("-")


def _magis_resource_name(binding: MagisBinding) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", binding.name.lower()).strip("-") or "magis"
    return f"magi-magis-{binding.id}-{slug}"[:55].rstrip("-")


def _secret_data(values: dict[str, str]) -> dict[str, str]:
    """Render binary-safe Secret data for server-side apply.

    Kubernetes documents ``stringData`` as incompatible with server-side
    apply field ownership. Encoding here makes repeated provider rotations
    deterministic and idempotent.
    """
    return {key: base64.b64encode(value.encode()).decode() for key, value in values.items()}


class KubernetesEveBackend:
    """Apply the fixed EVA resource template to one configured namespace."""

    def __init__(self) -> None:
        host = os.environ.get("KUBERNETES_SERVICE_HOST")
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        self.base_url = os.environ.get("MAGI_K8S_API_URL") or (
            f"https://{host}:{port}" if host else ""
        )
        self.namespace = os.environ.get("MAGI_K8S_NAMESPACE", "magi")
        self.image = os.environ.get("MAGI_EVA_IMAGE", "magi:0.1.0")
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

    def provision_magis(self, binding: MagisBinding) -> MagisProvisionResult:
        """Provision one MAGIS's private PostgreSQL and public workspace.

        The deterministic password prevents an idempotent reconciliation from
        rotating credentials underneath an existing PostgreSQL volume. It is
        written only to a Kubernetes Secret; MAGI nodes receive the DSN from
        that Secret, never from an instruction/config environment payload.
        """
        resource = _magis_resource_name(binding)
        database_service = f"{resource}-db"
        database_claim = f"{resource}-db-data"
        workspace_claim = f"{resource}-workspace"
        secret_name = f"{resource}-db"
        password = hmac.new(
            os.environ.get("MAGI_CONTROL_SECRET", "").encode(),
            f"magis-db:{binding.id}".encode(), hashlib.sha256,
        ).hexdigest()
        database = f"magis_{binding.id}"
        database_url = f"postgresql+psycopg://magi:{password}@{database_service}:5432/{database}"
        labels = {
            "app.kubernetes.io/name": "magi",
            "app.kubernetes.io/component": "magis-database",
            "magi.io/managed-by": "magi-orchestrator",
            "magi.io/magis-id": str(binding.id),
        }
        prefix = f"/api/v1/namespaces/{self.namespace}"
        self._apply(f"{prefix}/secrets/{secret_name}", {
            "apiVersion": "v1", "kind": "Secret", "metadata": {"name": secret_name, "labels": labels},
            "type": "Opaque", "data": _secret_data({"POSTGRES_PASSWORD": password, "MAGIS_DATABASE_URL": database_url}),
        })
        for claim, component in ((database_claim, "magis-database"), (workspace_claim, "magis-workspace")):
            self._apply(f"{prefix}/persistentvolumeclaims/{claim}", {
                "apiVersion": "v1", "kind": "PersistentVolumeClaim",
                "metadata": {"name": claim, "labels": {**labels, "app.kubernetes.io/component": component}},
                "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "10Gi"}}},
            })
        self._apply(f"{prefix}/services/{database_service}", {
            "apiVersion": "v1", "kind": "Service", "metadata": {"name": database_service, "labels": labels},
            "spec": {"selector": {"magi.io/magis-db": resource}, "ports": [{"name": "postgres", "port": 5432, "targetPort": "postgres"}]},
        })
        self._apply(f"/apis/apps/v1/namespaces/{self.namespace}/deployments/{database_service}", {
            "apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": database_service, "labels": labels},
            "spec": {"replicas": 1, "strategy": {"type": "Recreate"}, "selector": {"matchLabels": {"magi.io/magis-db": resource}}, "template": {"metadata": {"labels": {**labels, "magi.io/magis-db": resource}}, "spec": {"securityContext": {"fsGroup": 999}, "containers": [{"name": "postgres", "image": "postgres:16-alpine", "ports": [{"name": "postgres", "containerPort": 5432}], "env": [{"name": "POSTGRES_USER", "value": "magi"}, {"name": "POSTGRES_DB", "value": database}, {"name": "POSTGRES_PASSWORD", "valueFrom": {"secretKeyRef": {"name": secret_name, "key": "POSTGRES_PASSWORD"}}}], "volumeMounts": [{"name": "data", "mountPath": "/var/lib/postgresql/data"}], "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}}], "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": database_claim}}]}}},
        })
        return MagisProvisionResult(database_service_name=database_service, workspace_claim_name=workspace_claim, message="MAGIS PostgreSQL and public workspace PVC applied.")

    def _magis_database_url(self, binding: MagisBinding) -> str:
        resource = _magis_resource_name(binding)
        try:
            secret = self._request(
                "GET",
                f"/api/v1/namespaces/{self.namespace}/secrets/{resource}-db",
                content_type="application/json",
            )
            encoded = (secret.get("data") or {}).get("MAGIS_DATABASE_URL")
            if encoded:
                return base64.b64decode(encoded).decode()
        except Exception:
            # New MAGIS provision uses the deterministic fallback below. The
            # explicit Secret remains authoritative whenever it exists.
            pass
        password = hmac.new(
            os.environ.get("MAGI_CONTROL_SECRET", "").encode(),
            f"magis-db:{binding.id}".encode(), hashlib.sha256,
        ).hexdigest()
        return f"postgresql+psycopg://magi:{password}@{resource}-db:5432/magis_{binding.id}"

    def _project_runtime_configuration(self, spec: EvaSpec) -> None:
        """Write a MAGI's direct-MAGIS configuration before Pod creation.

        The target database is a runtime projection. The ADAM control plane
        remains the authority for the MAGIS tree; a container only receives
        the one direct MAGIS record it needs to operate.
        """
        if spec.magis is None or spec.configuration is None:
            return
        from magi.bus import get_bus
        from magi.bus.services.magis import MagisService

        projection = MagisService.RuntimeConfigurationProjection(
            magis_id=spec.magis.id,
            magis_name=spec.magis.name,
            magic_id=spec.magic_id,
            magic_name=spec.configuration.magic_name,
            personal_instruction=spec.configuration.personal_instruction,
            provider=spec.configuration.provider,
            api_key=spec.configuration.api_key,
            role_name=spec.configuration.role_name,
            role_instruction=spec.configuration.role_instruction,
            magis_instruction=spec.configuration.magis_instruction,
        )
        MagisService.project_runtime_configuration(
            projection,
            self._magis_database_url(spec.magis),
        )
        _ = bootstrap  # noqa: F841  # composition-root convention: import kept for explicit traceability

    def start(self, spec: EvaSpec) -> EvaOperationResult:
        name = _resource_name(spec)
        pvc_name = f"{name}-workspace"
        labels = {
            "app.kubernetes.io/name": "magi",
            "app.kubernetes.io/component": "eva",
            "magi.io/managed-by": "magi-orchestrator",
            "magi.io/magic-id": str(spec.magic_id),
        }
        prefix = f"/api/v1/namespaces/{self.namespace}"
        if spec.magis is None:
            raise ValueError("starting a MAGI requires one direct MAGIS binding")
        self._project_runtime_configuration(spec)
        magis_resource = _magis_resource_name(spec.magis)
        magis_workspace_claim = f"{magis_resource}-workspace"
        magis_database_secret = f"{magis_resource}-db"
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
        # The browser never reaches this Service. The singleton WebUI control
        # plane uses it to proxy authenticated requests to this MAGI runtime.
        self._apply(
            f"{prefix}/services/{name}",
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": name, "labels": labels},
                "spec": {
                    "selector": {"magi.io/magic-id": str(spec.magic_id)},
                    "ports": [{"name": "http", "port": 42069, "targetPort": "http"}],
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
                    "selector": {"matchLabels": {"magi.io/magic-id": str(spec.magic_id)}},
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
                                        # The runtime derives its archetype (ADAM
                                        # vs EVA) from MAGIS at boot via
                                        # ``MagisService.derive_runtime_role`` —
                                        # see ``MAGI_RUNTIME_ID`` → MAGIC.id →
                                        # ``MAGIS.adam_id`` comparison. No
                                        # separate role env var is needed.
                                        {"name": "MAGI_RUNTIME_ID", "value": str(spec.magic_id)},
                                        {"name": "MAGIS_ID", "value": str(spec.magis.id)},
                                        {"name": "MAGIS_DATABASE_URL", "valueFrom": {"secretKeyRef": {"name": magis_database_secret, "key": "MAGIS_DATABASE_URL"}}},
                                        {"name": "MAGI_CONTROL_SECRET", "valueFrom": {"secretKeyRef": {"name": "magi-control", "key": "MAGI_CONTROL_SECRET"}}},
                                    ],
                                    "volumeMounts": [{"name": "workspace", "mountPath": "/workspace"}, {"name": "magis-workspace", "mountPath": "/magis"}],
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
                                ,{"name": "magis-workspace", "persistentVolumeClaim": {"claimName": magis_workspace_claim}}
                            ],
                        },
                    },
                },
            },
        )
        return EvaOperationResult(
            observed_state="provisioning",
            namespace=self.namespace,
            deployment_name=name,
            workspace_claim_name=pvc_name,
            credential_secret_name=None,
            message="MAGI Deployment applied; it resolves configuration from its direct MAGIS database.",
        )

    def stop(self, spec: EvaSpec) -> EvaOperationResult:
        name = _resource_name(spec)
        self._request(
            "PATCH",
            f"/apis/apps/v1/namespaces/{self.namespace}/deployments/{name}",
            body={"spec": {"replicas": 0}},
            content_type="application/merge-patch+json",
        )
        return EvaOperationResult(
            observed_state="stopped",
            namespace=self.namespace,
            deployment_name=name,
            workspace_claim_name=f"{name}-workspace",
            credential_secret_name=None,
            message="MAGI scaled to zero; its private and MAGIS public workspaces were retained.",
        )

    def delete(self, spec: EvaSpec) -> EvaOperationResult:
        """Remove the managed resource set after an explicit Admin delete."""
        name = _resource_name(spec)
        prefix = f"/api/v1/namespaces/{self.namespace}"
        self._delete(f"/apis/apps/v1/namespaces/{self.namespace}/deployments/{name}")
        self._delete(f"{prefix}/services/{name}")
        self._delete(f"{prefix}/persistentvolumeclaims/{name}-workspace")
        return EvaOperationResult(
            observed_state="deleted",
            namespace=self.namespace,
            deployment_name=name,
            workspace_claim_name=f"{name}-workspace",
            credential_secret_name=None,
            message="MAGI Deployment and private workspace PVC were deleted.",
        )

"""Kubernetes resource creation — plan §17.

Creates per-MAGI PVC + Deployment + (optional) WebUI Deployment +
external Service. There is **no** Backend abstraction layer — the
Kubernetes path lives directly here per plan §6 and §17.

This module also owns :class:`KubernetesEvaBackend`, the in-process
K8s resource client used by the orchestrator service in
:mod:`magi.startup.kubernetes.service` (consolidated from the
legacy ``magi.orchestrator.kubernetes`` per plan §20.4).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
from pathlib import Path
from typing import Any

import httpx

from magi.startup.kubernetes._secret import get_control_secret

from magi.startup.config import (
    DEFAULT_MAGI_NAME,
    RUNTIME_PORT,
    WEBUI_PORT,
    StartupConfig,
)
from magi.startup.kubernetes.contracts import (
    EvaOperationResult,
    MagisProvisionResult,
)

logger = logging.getLogger("magi.startup.kubernetes")


# ----------------------------------------------------------------------
# Resource naming
# ----------------------------------------------------------------------


def _slug(value: str | None, fallback: str = "eva") -> str:
    raw = (value or fallback).lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", raw).strip("-") or fallback
    return slug[:55].rstrip("-")


def _eva_resource_name(magi_id: int | str, name: str) -> str:
    return f"magi-eva-{magi_id}-{_slug(name, 'eva')}"[:63].rstrip("-")


def _magis_resource_name(magis_id: int | str, name: str) -> str:
    return f"magi-magis-{magis_id}-{_slug(name, 'magis')}"[:55].rstrip("-")


def _shared_magis_database_resource_name() -> str:
    """The one PostgreSQL service that contains one database per MAGIS."""
    return "magi-magis-postgres"


def _webui_resource_name() -> str:
    return "magi-webui"


# ----------------------------------------------------------------------
# Image + namespace
# ----------------------------------------------------------------------


def _image() -> str:
    import os

    return os.environ.get("MAGI_IMAGE", "magi:0.1.0")


def _namespace() -> str:
    import os

    return os.environ.get("MAGI_K8S_NAMESPACE", "magi")


# ----------------------------------------------------------------------
# Public: per-MAGI deploy
# ----------------------------------------------------------------------


def create_magi_resources(*, config: StartupConfig, magi_id: int) -> dict[str, Any]:
    """Build the manifests for one MAGI's PVC + Service + Deployment.

    Returns a dict with the three manifest documents; the caller (the
    CLI verb) is responsible for applying them via the legacy
    :mod:`magi.orchestrator.kubernetes` client.

    The Service is intentionally ClusterIP (internal-only) per
    plan §15 — only the singleton WebUI is externally exposed.
    The Service ``port`` / ``targetPort`` forward to the Runtime's
    internal port from :data:`magi.startup.config.RUNTIME_PORT`,
    *not* the operator-facing WebUI port.
    """
    if config.is_first_magi and config.magi_name != DEFAULT_MAGI_NAME:
        raise ValueError(f"first MAGI must be {DEFAULT_MAGI_NAME}")
    name = _eva_resource_name(magi_id, config.magi_name)
    pvc_name = f"{name}-workspace"
    ns = _namespace()
    runtime_port = RUNTIME_PORT
    return {
        "pvc": {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": pvc_name, "namespace": ns},
            "spec": {
                "accessModes": ["ReadWriteOnce"],
                "resources": {"requests": {"storage": "10Gi"}},
            },
        },
        "service": {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": name, "namespace": ns},
            "spec": {
                "selector": {"magi.io/magi-id": str(magi_id)},
                # ClusterIP is the default; do not mark externally
                # reachable (plan §15 — only the WebUI is).
                "ports": [{"name": "http", "port": runtime_port, "targetPort": runtime_port}],
            },
        },
        "deployment": {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": name, "namespace": ns},
            "spec": {
                "replicas": 1,
                "strategy": {"type": "Recreate"},
                "selector": {"matchLabels": {"magi.io/magi-id": str(magi_id)}},
                "template": {
                    "metadata": {"labels": {"magi.io/magi-id": str(magi_id)}},
                    "spec": {
                        "containers": [
                            {
                                "name": "magi",
                                "image": _image(),
                                "env": [
                                    {"name": "HOST_WORKSPACE_DIR", "value": "/workspace"},
                                    {"name": "MAGI_NAME", "value": config.magi_name},
                                    {"name": "MAGI_ID", "value": str(magi_id)},
                                    {
                                        "name": "MAGIS_DATABASE_URL",
                                        "value": config.magis_database_url or "",
                                    },
                                ],
                                "volumeMounts": [{"name": "workspace", "mountPath": "/workspace"}],
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
    }


def create_magis_resources(
    *,
    config: StartupConfig,
    magis_id: int,
    magis_name: str,
) -> dict[str, Any]:
    """Build the per-MAGIS database + workspace manifests."""
    _ = config
    name = _magis_resource_name(magis_id, magis_name)
    ns = _namespace()
    return {
        "pvc": {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": f"{name}-workspace", "namespace": ns},
            "spec": {
                "accessModes": ["ReadWriteOnce"],
                "resources": {"requests": {"storage": "10Gi"}},
            },
        },
    }


# ----------------------------------------------------------------------
# WebUI
# ----------------------------------------------------------------------


def ensure_webui_deployment(*, config: StartupConfig) -> dict[str, Any]:
    """Singleton WebUI Deployment manifest (plan §17)."""
    name = _webui_resource_name()
    ns = _namespace()
    return {
        "deployment": {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": name, "namespace": ns},
            "spec": {
                "replicas": 1,
                "strategy": {"type": "Recreate"},
                "selector": {"matchLabels": {"app": "magi-webui"}},
                "template": {
                    "metadata": {"labels": {"app": "magi-webui"}},
                    "spec": {
                        "containers": [
                            {
                                "name": "webui",
                                "image": _image(),
                                "command": ["magi"],
                                "args": ["webui"],
                                "env": [
                                    {
                                        "name": "HOST_WORKSPACE_DIR",
                                        "value": "/workspace",
                                    },
                                    {
                                        "name": "MAGIS_DATABASE_URL",
                                        "value": config.magis_database_url or "",
                                    },
                                    {
                                        # Plan §15 — WebUI is the only
                                        # externally routable surface;
                                        # its port is hardcoded.
                                        "name": "MAGI_WEBUI_PORT",
                                        "value": str(WEBUI_PORT),
                                    },
                                    {
                                        "name": "MAGI_WEBUI_HOST",
                                        "value": "0.0.0.0",
                                    },
                                ],
                            }
                        ],
                    },
                },
            },
        },
    }


def ensure_webui_service(*, config: StartupConfig) -> dict[str, Any]:
    """Internal Service for the singleton WebUI.

    Plan §15 — this is the operator-facing surface, but the canonical
    deployment uses a ``ClusterIP`` plus a NodePort / port-forward, not
    a cloud LoadBalancer (matches ``deploy/k8s/control/webui-service.yaml``).
    ``port`` / ``targetPort`` forward to the WebUI port, never the
    Runtime port.
    """
    _ = config
    name = _webui_resource_name()
    ns = _namespace()
    return {
        "service": {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": name, "namespace": ns},
            "spec": {
                "type": "ClusterIP",
                "selector": {"app": "magi-webui"},
                "ports": [
                    {
                        "name": "http",
                        "port": WEBUI_PORT,
                        "targetPort": WEBUI_PORT,
                    }
                ],
            },
        },
    }


def delete_webui_resources(*, config: StartupConfig) -> None:
    """Delete the WebUI Deployment + Service.

    Plan §15 — only the singleton WebUI is touched. Per-MAGI resources
    are managed by ``create_magi_resources``.
    """
    _ = config
    name = _webui_resource_name()
    logger.info("deleting singleton WebUI resources: %s", name)
    try:
        backend = KubernetesEvaBackend()
        backend._delete(f"/apis/apps/v1/namespaces/{_namespace()}/deployments/{name}")
        backend._delete(f"/api/v1/namespaces/{_namespace()}/services/{name}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("delete_webui_resources skipped: %s", exc)


# ----------------------------------------------------------------------
# KubernetesEvaBackend — the in-process K8s resource client used by the
# orchestrator service. Consolidated from the legacy
# ``magi.orchestrator.kubernetes.KubernetesEvaBackend`` per plan §20.4.
# ----------------------------------------------------------------------

_TOKEN_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
_CA_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")


def _k8s_resource_name(spec_or_binding) -> str:
    raw = (getattr(spec_or_binding, "name", None) or "eva").lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", raw).strip("-") or "eva"
    return f"magi-eva-{spec_or_binding.magi_id}-{slug}"[:63].rstrip("-")


def _magis_k8s_resource_name(binding) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", binding.name.lower()).strip("-") or "magis"
    return f"magi-magis-{binding.id}-{slug}"[:55].rstrip("-")


def _k8s_secret_data(values: dict[str, str]) -> dict[str, str]:
    """Render binary-safe Secret data for server-side apply."""
    return {key: base64.b64encode(value.encode()).decode() for key, value in values.items()}


class KubernetesEvaBackend:
    """Apply the fixed EVA resource template to one configured namespace.

    Consolidated from the legacy ``magi.orchestrator.kubernetes``
    module per plan §20.4 — the orchestrator service in
    :mod:`magi.startup.kubernetes.service` is the only caller.
    """

    def __init__(self) -> None:
        host = os.environ.get("KUBERNETES_SERVICE_HOST")
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        self.base_url = os.environ.get("MAGI_K8S_API_URL") or (
            f"https://{host}:{port}" if host else ""
        )
        self.namespace = os.environ.get("MAGI_K8S_NAMESPACE", "magi")
        self.image = os.environ.get("MAGI_IMAGE", "magi:0.1.0")
        if not self.base_url or not _TOKEN_PATH.is_file():
            raise RuntimeError("Kubernetes service-account credentials are unavailable")
        self.token = _TOKEN_PATH.read_text().strip()
        self.verify: bool | str = str(_CA_PATH) if _CA_PATH.is_file() else True

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        content_type: str,
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

    def provision_magis(self, binding) -> MagisProvisionResult:
        """Provision a MAGIS database in the shared PostgreSQL service.

        A MAGIS is a database boundary, not a PostgreSQL-server boundary.
        The server/PVC/credentials are shared; each MAGIS receives a distinct
        database and connection secret, plus its own public workspace PVC.
        """
        resource = _magis_k8s_resource_name(binding)
        database_service = _shared_magis_database_resource_name()
        database_claim = f"{database_service}-data"
        shared_secret = f"{database_service}-credentials"
        magis_secret = f"{resource}-db"
        workspace_claim = f"{resource}-workspace"
        secret = get_control_secret()
        if not secret:
            raise RuntimeError(
                "control secret is not configured (neither MAGIS_DATABASE_URL "
                "control_secrets row nor MAGI_CONTROL_SECRET env var); "
                "ensure the cluster bootstrap has run before provisioning a MAGIS."
            )
        password = hmac.new(
            secret, b"magis-postgres", hashlib.sha256
        ).hexdigest()
        database = f"magis_{binding.id}"
        database_url = f"postgresql+psycopg://magi:{password}@{database_service}:5432/{database}"
        labels = {
            "app.kubernetes.io/name": "magi",
            "magi.io/managed-by": "magi-orchestrator",
        }
        prefix = f"/api/v1/namespaces/{self.namespace}"
        self._apply(
            f"{prefix}/secrets/{shared_secret}",
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {
                    "name": shared_secret,
                    "labels": {**labels, "app.kubernetes.io/component": "magis-database"},
                },
                "type": "Opaque",
                "data": _k8s_secret_data({"POSTGRES_PASSWORD": password}),
            },
        )
        self._apply(
            f"{prefix}/persistentvolumeclaims/{database_claim}",
            {
                "apiVersion": "v1",
                "kind": "PersistentVolumeClaim",
                "metadata": {
                    "name": database_claim,
                    "labels": {**labels, "app.kubernetes.io/component": "magis-database"},
                },
                "spec": {
                    "accessModes": ["ReadWriteOnce"],
                    "resources": {"requests": {"storage": "10Gi"}},
                },
            },
        )
        self._apply(
            f"{prefix}/services/{database_service}",
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": database_service, "labels": labels},
                "spec": {
                    "selector": {"magi.io/magis-db": "shared"},
                    "ports": [{"name": "postgres", "port": 5432, "targetPort": "postgres"}],
                },
            },
        )
        self._apply(
            f"/apis/apps/v1/namespaces/{self.namespace}/deployments/{database_service}",
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": database_service, "labels": labels},
                "spec": {
                    "replicas": 1,
                    "strategy": {"type": "Recreate"},
                    "selector": {"matchLabels": {"magi.io/magis-db": "shared"}},
                    "template": {
                        "metadata": {"labels": {**labels, "magi.io/magis-db": "shared"}},
                        "spec": {
                            "securityContext": {"fsGroup": 999},
                            "containers": [
                                {
                                    "name": "postgres",
                                    "image": "postgres:16-alpine",
                                    "ports": [{"name": "postgres", "containerPort": 5432}],
                                    "env": [
                                        {"name": "POSTGRES_USER", "value": "magi"},
                                        {"name": "POSTGRES_DB", "value": "postgres"},
                                        {
                                            "name": "POSTGRES_PASSWORD",
                                            "valueFrom": {
                                                "secretKeyRef": {
                                                    "name": shared_secret,
                                                    "key": "POSTGRES_PASSWORD",
                                                }
                                            },
                                        },
                                    ],
                                    "volumeMounts": [
                                        {"name": "data", "mountPath": "/var/lib/postgresql/data"}
                                    ],
                                    "securityContext": {
                                        "allowPrivilegeEscalation": False,
                                        "capabilities": {"drop": ["ALL"]},
                                    },
                                }
                            ],
                            "volumes": [
                                {
                                    "name": "data",
                                    "persistentVolumeClaim": {"claimName": database_claim},
                                }
                            ],
                        },
                    },
                },
            },
        )
        self._apply(
            f"{prefix}/secrets/{magis_secret}",
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {
                    "name": magis_secret,
                    "labels": {**labels, "magi.io/magis-id": str(binding.id)},
                },
                "type": "Opaque",
                "data": _k8s_secret_data({"MAGIS_DATABASE_URL": database_url}),
            },
        )
        self._apply(
            f"{prefix}/persistentvolumeclaims/{workspace_claim}",
            {
                "apiVersion": "v1",
                "kind": "PersistentVolumeClaim",
                "metadata": {
                    "name": workspace_claim,
                    "labels": {
                        **labels,
                        "app.kubernetes.io/component": "magis-workspace",
                        "magi.io/magis-id": str(binding.id),
                    },
                },
                "spec": {
                    "accessModes": ["ReadWriteOnce"],
                    "resources": {"requests": {"storage": "10Gi"}},
                },
            },
        )
        self._apply(
            f"/apis/batch/v1/namespaces/{self.namespace}/jobs/{resource}-db-init",
            {
                "apiVersion": "batch/v1",
                "kind": "Job",
                "metadata": {
                    "name": f"{resource}-db-init",
                    "labels": {**labels, "magi.io/magis-id": str(binding.id)},
                },
                "spec": {
                    "backoffLimit": 12,
                    "template": {
                        "metadata": {"labels": labels},
                        "spec": {
                            "restartPolicy": "OnFailure",
                            "containers": [
                                {
                                    "name": "create-database",
                                    "image": "postgres:16-alpine",
                                    "command": [
                                        "sh",
                                        "-ec",
                                        f'psql -h {database_service} -U magi -d postgres -tc "SELECT 1 FROM pg_database WHERE datname = \'{database}\'" | grep -q 1 || psql -h {database_service} -U magi -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE {database}"',
                                    ],
                                    "env": [
                                        {
                                            "name": "PGPASSWORD",
                                            "valueFrom": {
                                                "secretKeyRef": {
                                                    "name": shared_secret,
                                                    "key": "POSTGRES_PASSWORD",
                                                }
                                            },
                                        }
                                    ],
                                    "securityContext": {
                                        "allowPrivilegeEscalation": False,
                                        "capabilities": {"drop": ["ALL"]},
                                    },
                                }
                            ],
                        },
                    },
                },
            },
        )
        return MagisProvisionResult(
            database_service_name=database_service,
            workspace_claim_name=workspace_claim,
            message="MAGIS database, shared PostgreSQL service, and public workspace PVC applied.",
        )

    def _magis_database_url(self, binding) -> str:
        resource = _magis_k8s_resource_name(binding)
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
            pass
        secret = get_control_secret()
        if not secret:
            return (
                f"postgresql+psycopg://magi:missing@{_shared_magis_database_resource_name()}:5432/"
                f"magis_{binding.id}"
            )
        password = hmac.new(
            secret, b"magis-postgres", hashlib.sha256
        ).hexdigest()
        return (
            f"postgresql+psycopg://magi:{password}@{_shared_magis_database_resource_name()}:5432/"
            f"magis_{binding.id}"
        )

    def start(self, spec) -> EvaOperationResult:
        name = _k8s_resource_name(spec)
        pvc_name = f"{name}-workspace"
        labels = {
            "app.kubernetes.io/name": "magi",
            "app.kubernetes.io/component": "eva",
            "magi.io/managed-by": "magi-orchestrator",
            "magi.io/magi-id": str(spec.magi_id),
        }
        prefix = f"/api/v1/namespaces/{self.namespace}"
        if spec.magis is None:
            raise ValueError("starting a MAGI requires one direct MAGIS binding")
        magis_resource = _magis_k8s_resource_name(spec.magis)
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
        self._apply(
            f"{prefix}/services/{name}",
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": name, "labels": labels},
                "spec": {
                    "selector": {"magi.io/magi-id": str(spec.magi_id)},
                    # The Service forwards to the Runtime's internal
                    # port (plan §15 — no external MAGI exposure).
                    "ports": [
                        {
                            "name": "http",
                            "port": RUNTIME_PORT,
                            "targetPort": RUNTIME_PORT,
                        }
                    ],
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
                                        {
                                            "name": "MAGI_RUNTIME_ID",
                                            "value": str(spec.magi_id),
                                        },
                                        {"name": "MAGIS_ID", "value": str(spec.magis.id)},
                                        {
                                            "name": "MAGIS_DATABASE_URL",
                                            "valueFrom": {
                                                "secretKeyRef": {
                                                    "name": magis_database_secret,
                                                    "key": "MAGIS_DATABASE_URL",
                                                }
                                            },
                                        },
                                        {
                                            # Plan §4 — the four startup-
                                            # contract inputs come from the
                                            # orchestrator. Plan §21 — Runtime
                                            # port is hardcoded internally.
                                            "name": "HOST_WORKSPACE_DIR",
                                            "value": "/workspace",
                                        },
                                        {
                                            "name": "MAGI_NAME",
                                            "value": getattr(spec, "name", "") or "eva",
                                        },
                                        {
                                            "name": "MAGI_CONTROL_SECRET",
                                            "valueFrom": {
                                                "secretKeyRef": {
                                                    "name": "magi-control",
                                                    "key": "MAGI_CONTROL_SECRET",
                                                }
                                            },
                                        },
                                    ],
                                    "volumeMounts": [
                                        {
                                            "name": "workspace",
                                            "mountPath": "/workspace",
                                        },
                                        {
                                            "name": "magis-workspace",
                                            "mountPath": "/magis",
                                        },
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
                                },
                                {
                                    "name": "magis-workspace",
                                    "persistentVolumeClaim": {"claimName": magis_workspace_claim},
                                },
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

    def stop(self, spec) -> EvaOperationResult:
        name = _k8s_resource_name(spec)
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

    def delete(self, spec) -> EvaOperationResult:
        """Remove the managed resource set after an explicit Admin delete."""
        name = _k8s_resource_name(spec)
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


__all__ = [
    "create_magi_resources",
    "create_magis_resources",
    "ensure_webui_deployment",
    "ensure_webui_service",
    "delete_webui_resources",
    "KubernetesEvaBackend",
]

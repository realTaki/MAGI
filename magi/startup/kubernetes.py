"""Kubernetes resource creation — plan §17.

Creates per-MAGI PVC + Deployment + (optional) WebUI Deployment +
external Service. There is **no** Backend abstraction layer — the
Kubernetes path lives directly here per plan §6 and §17.

This module is intentionally thin and only assembles the resource
manifests; the actual Kubernetes API client lives in the legacy
``magi.orchestrator.kubernetes`` module (kept for diagnostic value).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from magi.startup.config import StartupConfig

logger = logging.getLogger("magi.startup.kubernetes")


# ----------------------------------------------------------------------
# Resource naming
# ----------------------------------------------------------------------


def _slug(value: str | None, fallback: str = "eva") -> str:
    raw = (value or fallback).lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", raw).strip("-") or fallback
    return slug[:55].rstrip("-")


def _eva_resource_name(magic_id: int | str, name: str) -> str:
    return f"magi-eva-{magic_id}-{_slug(name, 'eva')}"[:63].rstrip("-")


def _magis_resource_name(magis_id: int | str, name: str) -> str:
    return f"magi-magis-{magis_id}-{_slug(name, 'magis')}"[:55].rstrip("-")


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


def create_magi_resources(*, config: StartupConfig, magic_id: int) -> dict[str, Any]:
    """Build the manifests for one MAGI's PVC + Service + Deployment.

    Returns a dict with the three manifest documents; the caller (the
    CLI verb) is responsible for applying them via the legacy
    :mod:`magi.orchestrator.kubernetes` client.
    """
    if config.is_first_magi and config.magi_name != "eva-000":
        raise ValueError("first MAGI must be eva-000")
    name = _eva_resource_name(magic_id, config.magi_name)
    pvc_name = f"{name}-workspace"
    ns = _namespace()
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
                    "selector": {"magi.io/magic-id": str(magic_id)},
                    "ports": [{"name": "http", "port": 42069, "targetPort": 42069}],
                },
            },
        "deployment": {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": name, "namespace": ns},
                "spec": {
                    "replicas": 1,
                    "strategy": {"type": "Recreate"},
                    "selector": {"matchLabels": {"magi.io/magic-id": str(magic_id)}},
                    "template": {
                        "metadata": {"labels": {"magi.io/magic-id": str(magic_id)}},
                        "spec": {
                            "containers": [
                                {
                                    "name": "magi",
                                    "image": _image(),
                                    "env": [
                                        {"name": "HOST_WORKSPACE_DIR", "value": "/workspace"},
                                        {"name": "MAGI_NAME", "value": config.magi_name},
                                        {"name": "MAGI_ID", "value": str(magic_id)},
                                        {
                                            "name": "MAGIS_DATABASE_URL",
                                            "value": config.magis_database_url
                                            or "",
                                        },
                                    ],
                                    "volumeMounts": [
                                        {"name": "workspace", "mountPath": "/workspace"}
                                    ],
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
                                ],
                            }
                        ],
                    },
                },
            },
        },
    }


def ensure_webui_service(*, config: StartupConfig) -> dict[str, Any]:
    """External Service for the singleton WebUI."""
    name = _webui_resource_name()
    ns = _namespace()
    return {
        "service": {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": name, "namespace": ns},
            "spec": {
                "type": "LoadBalancer",
                "selector": {"app": "magi-webui"},
                "ports": [{"name": "http", "port": 42069, "targetPort": 42069}],
            },
        },
    }


def delete_webui_resources(*, config: StartupConfig) -> None:
    """Delete the WebUI Deployment + Service.

    Plan §15 — only the singleton WebUI is touched. Per-MAGI resources
    are managed by ``create_magi_resources``.
    """
    name = _webui_resource_name()
    logger.info("deleting singleton WebUI resources: %s", name)
    # The actual DELETE call is delegated to the legacy K8s client so
    # the auth / RBAC story stays in one place.  Kept thin here.
    try:
        from magi.orchestrator.kubernetes import KubernetesEvaBackend

        backend = KubernetesEvaBackend()
        backend._delete(f"/apis/apps/v1/namespaces/{_namespace()}/deployments/{name}")
        backend._delete(f"/api/v1/namespaces/{_namespace()}/services/{name}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("delete_webui_resources skipped: %s", exc)


__all__ = [
    "create_magi_resources",
    "create_magis_resources",
    "ensure_webui_deployment",
    "ensure_webui_service",
    "delete_webui_resources",
]
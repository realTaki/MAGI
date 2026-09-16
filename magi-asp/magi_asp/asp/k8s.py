"""Create one MAGI Pod. magi-asp calls this; the desktop does not."""

from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .spawn import SpawnedMagi

CreatePod = Callable[[dict[str, Any]], None]

_HANDLE = re.compile(r"[^a-z0-9-]+")


def magi_pod_name(handle: str) -> str:
    raw = handle.lower().strip()
    if raw.startswith("@"):
        raw = raw[1:]
    if raw.endswith(".magi"):
        raw = raw[: -len(".magi")]
    slug = _HANDLE.sub("-", raw).strip("-") or "magi"
    return f"magi-{slug}"[:63]


def magi_pod(*, handle: str, base: str, token: str) -> dict[str, Any]:
    """One container per MAGI. Command is ``magi <handle> <base> <token>``."""
    name = magi_pod_name(handle)
    image = os.environ.get("MAGI_IMAGE", "magi:0.1.0")
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "labels": {
                "app.kubernetes.io/name": "magi",
                "app.kubernetes.io/component": "magi",
                "magi.asp/handle": handle,
            },
        },
        "spec": {
            "restartPolicy": "Always",
            "containers": [
                {
                    "name": "magi",
                    "image": image,
                    "imagePullPolicy": os.environ.get("MAGI_IMAGE_PULL", "IfNotPresent"),
                    "command": ["magi"],
                    "args": [handle, base, token],
                    "env": [{"name": "PYTHONUNBUFFERED", "value": "1"}],
                }
            ],
        },
    }


def create_namespaced_pod(pod: dict[str, Any]) -> None:
    namespace = _namespace()
    host = os.environ["KUBERNETES_SERVICE_HOST"]
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    token = Path("/var/run/secrets/kubernetes.io/serviceaccount/token").read_text()
    ctx = ssl.create_default_context()
    ca = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
    if ca.exists():
        ctx.load_verify_locations(ca)
    request = urllib.request.Request(
        f"https://{host}:{port}/api/v1/namespaces/{namespace}/pods",
        data=json.dumps(pod).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        urllib.request.urlopen(request, context=ctx, timeout=15)
    except urllib.error.HTTPError as exc:
        if exc.code != 409:
            raise


def _namespace() -> str:
    env = os.environ.get("MAGI_NAMESPACE")
    if env:
        return env
    path = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
    if path.exists():
        return path.read_text().strip() or "magi"
    return "magi"


class KubernetesSpawner:
    """Each MAGI start is its own k8s container. ASP owns create; not the desktop."""

    def __init__(self, create_pod: CreatePod | None = None) -> None:
        self._create_pod = create_pod if create_pod is not None else create_namespaced_pod
        self.pods: list[str] = []

    def spawn(self, *, handle: str, base: str, token: str) -> SpawnedMagi:
        if os.environ.get("MAGI_SPAWN", "1").strip().lower() in {"0", "false", "no", "off"}:
            return SpawnedMagi(handle=handle, token=token, pid=None, spawned=False)
        pod = magi_pod(handle=handle, base=base, token=token)
        try:
            self._create_pod(pod)
        except (OSError, urllib.error.URLError, urllib.error.HTTPError):
            return SpawnedMagi(handle=handle, token=token, pid=None, spawned=False)
        self.pods.append(pod["metadata"]["name"])
        return SpawnedMagi(handle=handle, token=token, pid=None, spawned=True)

    def close(self) -> None:
        return None

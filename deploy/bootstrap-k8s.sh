#!/usr/bin/env bash
# Deploy the Genesis Adam and its restricted EVE orchestrator to an existing
# Kubernetes cluster. kubectl is downloaded into deploy/.tools when absent,
# never installed system-wide or via sudo.
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
TOOLS_DIR="$ROOT_DIR/deploy/.tools"
KUBECTL_VERSION="${KUBECTL_VERSION:-v1.31.0}"
MAGI_IMAGE="${MAGI_IMAGE:-magi:0.1.0}"
EVE_IMAGE="${EVE_IMAGE:-$MAGI_IMAGE}"
ADAM_OVERLAY="${ADAM_OVERLAY:-$ROOT_DIR/deploy/k8s/overlays/adam}"
ADAM_DEPLOYMENT="${ADAM_DEPLOYMENT:-magi-node}"
WEBUI_DEPLOYMENT="${WEBUI_DEPLOYMENT:-magi-webui}"
WEBUI_SERVICE="${WEBUI_SERVICE:-magi-webui}"
CONTROL_OVERLAY="${CONTROL_OVERLAY:-$ROOT_DIR/deploy/k8s/control}"
mkdir -p "$TOOLS_DIR"

if command -v kubectl >/dev/null 2>&1; then
  KUBECTL="$(command -v kubectl)"
else
  KUBECTL="$TOOLS_DIR/kubectl"
  if [ ! -x "$KUBECTL" ]; then
    os="$(uname -s | tr '[:upper:]' '[:lower:]')"
    arch="$(uname -m)"; [ "$arch" = "x86_64" ] && arch="amd64"; [ "$arch" = "aarch64" ] && arch="arm64"
    command -v curl >/dev/null || { echo "curl is required to download kubectl" >&2; exit 1; }
    curl --fail --location --silent --show-error "https://dl.k8s.io/release/$KUBECTL_VERSION/bin/$os/$arch/kubectl" -o "$KUBECTL"
    chmod 0755 "$KUBECTL"
  fi
fi

"$KUBECTL" cluster-info >/dev/null
"$KUBECTL" apply -f "$ROOT_DIR/deploy/k8s/namespace.yaml"
if ! "$KUBECTL" -n magi get secret magi-control >/dev/null 2>&1; then
  CONTROL_SECRET="$(openssl rand -hex 32)"
  "$KUBECTL" -n magi create secret generic magi-control --from-literal="MAGI_CONTROL_SECRET=$CONTROL_SECRET"
fi
"$KUBECTL" -n magi create configmap magi-orchestrator-config \
  --from-literal=MAGI_K8S_NAMESPACE=magi --from-literal=MAGI_EVE_IMAGE="$EVE_IMAGE" \
  --dry-run=client -o yaml | "$KUBECTL" apply -f -
"$KUBECTL" apply -k "$CONTROL_OVERLAY"
"$KUBECTL" apply -k "$ADAM_OVERLAY"
"$KUBECTL" -n magi set image deployment/$ADAM_DEPLOYMENT magi="$MAGI_IMAGE"
"$KUBECTL" -n magi set image deployment/$WEBUI_DEPLOYMENT magi="$MAGI_IMAGE"
"$KUBECTL" -n magi rollout status deployment/magi-orchestrator --timeout=180s
"$KUBECTL" -n magi rollout status deployment/$ADAM_DEPLOYMENT --timeout=180s
"$KUBECTL" -n magi rollout status deployment/$WEBUI_DEPLOYMENT --timeout=180s
echo "Genesis MAGI and unified WebUI are ready. Run: $KUBECTL -n magi port-forward svc/$WEBUI_SERVICE 42069:42069"

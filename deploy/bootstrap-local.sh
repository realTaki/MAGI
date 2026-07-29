#!/usr/bin/env bash
# One-command local development cluster bootstrap. Docker is the only host
# prerequisite; kind and kubectl are pinned inside deploy/.tools rather than
# system-installed. The kind node receives the checkout at /mnt/magi so the
# dev-alice overlay can hot-reload backend and WebUI source.
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
TOOLS_DIR="$ROOT_DIR/deploy/.tools"
KIND_VERSION="${KIND_VERSION:-v0.24.0}"
KUBECONFIG_PATH="${MAGI_KUBECONFIG:-$ROOT_DIR/.kind-kubeconfig}"
mkdir -p "$TOOLS_DIR"
command -v docker >/dev/null || { echo "Docker is required for local bootstrap" >&2; exit 1; }
KIND="$TOOLS_DIR/kind"
if [ ! -x "$KIND" ]; then
  os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  arch="$(uname -m)"; [ "$arch" = "x86_64" ] && arch="amd64"; [ "$arch" = "aarch64" ] && arch="arm64"
  command -v curl >/dev/null || { echo "curl is required to download kind" >&2; exit 1; }
  curl --fail --location --silent --show-error "https://kind.sigs.k8s.io/dl/$KIND_VERSION/kind-$os-$arch" -o "$KIND"
  chmod 0755 "$KIND"
fi
if ! "$KIND" get clusters | grep -qx magi; then
  sed "s|__MAGI_REPO_ROOT__|$ROOT_DIR|g" "$ROOT_DIR/deploy/k8s/kind.dev.yaml" \
    | "$KIND" create cluster --name magi --config=-
fi
"$KIND" export kubeconfig --name magi --kubeconfig "$KUBECONFIG_PATH" >/dev/null
docker build -f "$ROOT_DIR/deploy/Dockerfile" -t magi:0.1.0 "$ROOT_DIR"
docker build -f "$ROOT_DIR/deploy/Dockerfile.dev" -t magi:dev "$ROOT_DIR"
"$KIND" load docker-image magi:0.1.0 --name magi
"$KIND" load docker-image magi:dev --name magi
MAGI_IMAGE=magi:dev \
  EVE_IMAGE=magi:0.1.0 \
  ADAM_OVERLAY="$ROOT_DIR/deploy/k8s/overlays/dev-alice" \
  ADAM_DEPLOYMENT=alice-magi-node \
  ADAM_SERVICE=alice-magi \
  KUBECONFIG="$KUBECONFIG_PATH" \
  "$ROOT_DIR/deploy/bootstrap-k8s.sh"

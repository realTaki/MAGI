#!/usr/bin/env bash
# Single-node k8s dev deploy — start a kind cluster, build the production
# + dev images, and apply the dev overlays on top. Docker is the only host
# prerequisite; kind and kubectl are pinned inside deploy/.tools rather than
# system-installed. The kind node receives the checkout at /mnt/magi so the
# dev-eva00 overlay can hot-reload backend and WebUI source.
#
# Two related paths live alongside this file:
#
#   deploy/k8s-dev/bootstrap-k8s-dev.sh  ← this file
#   deploy/k8s/bootstrap-k8s.sh         ← production deploy to existing cluster
#   deploy/cli/                       ← non-container openclaw-style
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
TOOLS_DIR="$ROOT_DIR/deploy/.tools"
K8S_DIR="$ROOT_DIR/deploy/k8s"
K8S_DEV_DIR="$ROOT_DIR/deploy/k8s-dev"
KIND_VERSION="${KIND_VERSION:-v0.24.0}"
KUBECONFIG_PATH="${MAGI_KUBECONFIG:-$ROOT_DIR/.kind-kubeconfig}"

# OS-specific data root — matches the openclaw-style layout used by
# ``deploy/cli/magi`` and ``magi.startup.paths.resolve_host_workspace``.
resolve_data_root() {
  if [ -n "${HOST_WORKSPACE_DIR:-}" ]; then
    printf '%s\n' "$HOST_WORKSPACE_DIR"
    return
  fi
  case "$(uname -s)" in
    Darwin|MINGW*|MSYS*|CYGWIN*) printf '%s\n' "$HOME/Documents/.magi" ;;
    *)                           printf '%s\n' "$HOME/.magi" ;;
  esac
}
HOST_WORKSPACE_DIR="$(resolve_data_root)"
mkdir -p "$HOST_WORKSPACE_DIR"/{control,MAGI,MAGIS}

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
  sed -e "s|__MAGI_REPO_ROOT__|$ROOT_DIR|g" \
      -e "s|__HOST_WORKSPACE_DIR__|$HOST_WORKSPACE_DIR|g" \
      "$K8S_DEV_DIR/kind.yaml" \
    | "$KIND" create cluster --name magi --config=-
fi
"$KIND" export kubeconfig --name magi --kubeconfig "$KUBECONFIG_PATH" >/dev/null
docker build -f "$ROOT_DIR/deploy/Dockerfile" -t magi:0.1.0 "$ROOT_DIR"
docker build -f "$ROOT_DIR/deploy/Dockerfile.dev" -t magi:dev "$ROOT_DIR"
"$KIND" load docker-image magi:0.1.0 --name magi
"$KIND" load docker-image magi:dev --name magi
MAGI_IMAGE=magi:0.1.0 \
  ADAM_OVERLAY="$K8S_DEV_DIR/overlays/dev-eva00" \
  CONTROL_OVERLAY="$K8S_DEV_DIR/control-dev" \
  ADAM_DEPLOYMENT=magi-node \
  WEBUI_SERVICE=magi-webui \
  KUBECONFIG="$KUBECONFIG_PATH" \
  "$K8S_DIR/bootstrap-k8s.sh"

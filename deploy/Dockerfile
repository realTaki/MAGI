# syntax=docker/dockerfile:1.7
# One MAGI image, two selectable service roles:
#   - ``magi``       runs one MAGI runtime and its internal Runtime API;
#   - ``magi webui`` runs the singleton React/control-plane WebUI service.
#
# The SPA is deliberately baked into this one image so an installer downloads
# one artifact. Kubernetes chooses the role through the Deployment command;
# no MAGI runtime serves browser assets or a public WebUI endpoint.

# -----------------------------------------------------------------------------
# Stage 1: build the React SPA
# -----------------------------------------------------------------------------
FROM node:20-alpine AS web-builder

WORKDIR /web

# Install deps in a separate layer so source edits don't bust the cache.
COPY app/package.json app/package-lock.json* ./
RUN npm ci

COPY app ./
RUN npm run build
# /web/dist now contains the built SPA (index.html + assets/).

# -----------------------------------------------------------------------------
# Stage 2: install Python deps with uv
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS python-builder
COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /uvx /usr/local/bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app
# The Python project lives in py-magi/ and keeps its import package at magi/.
# uv finds pyproject at the project root and
# installs deps into the venv. README.md is also copied because
# pyproject declares it under [project] readme.
COPY py-magi/pyproject.toml py-magi/uv.lock* py-magi/README.md ./
COPY py-magi/magi ./magi
RUN uv sync --frozen --no-dev --extra adam --extra eva

# -----------------------------------------------------------------------------
# Stage 3: one runtime image — Python + prebuilt SPA, run as non-root magi user
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime
RUN useradd --create-home --shell /bin/bash magi \
    && mkdir -p /workspace/memories \
    && chown -R magi:magi /workspace

COPY --from=python-builder /opt/venv /opt/venv
COPY py-magi/magi /app/magi
# Prebuilt SPA is consumed only by ``magi webui``. It stays in the same image
# as MAGI runtimes to keep installation and versioning to one artifact.
COPY --from=web-builder /web/dist /app/dist

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /workspace
USER magi
# NOTE: we deliberately do NOT declare a VOLUME for /workspace/memories
# (or any subdir of /workspace). The compose file already bind-mounts
# the host's <MAGI_WORKSPACE_DIR>/ADAM onto /workspace, and a nested
# VOLUME directive would create an anonymous volume that shadows
# the bind-mounted subdirectory — making the host's data invisible
# to the container.

EXPOSE 42069

# Kubernetes supplies a role-specific HTTP probe. A single image-level probe
# cannot be correct for both runtime (42070) and control service (42069), so
# do not mask that distinction with a CLI invocation. The default starts one
# already-provisioned runtime; deployments may override it for init or WebUI.
CMD ["magi", "node", "run", "--foreground"]

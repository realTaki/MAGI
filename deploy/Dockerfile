# syntax=docker/dockerfile:1.7
# MAGI node — single image, role chosen at runtime via MAGI_NODE_ROLE.
#
# Every MAGI node (Adam or EVE) bundles both:
#   - the Python backend (magi runtime)
#   - the React SPA (magi/WebUI/dist), served by FastAPI at /
#
# That way, http://localhost:42069/ works the same on Adam and on an
# EVE container that mounted the webui channel — no external CDN, no
# separate static host, no "is webui available on this role?" question.
#
# Role (Adam / EVE) is selected at runtime via MAGI_NODE_ROLE; channel
# selection (webui / telegram) is via MAGI_CHANNELS. SQLite lives at
# /workspace/state (bind-mounted to the host).

# -----------------------------------------------------------------------------
# Stage 1: build the React SPA
# -----------------------------------------------------------------------------
FROM node:20-alpine AS web-builder

WORKDIR /web

# Install deps in a separate layer so source edits don't bust the cache.
COPY magi/WebUI/package.json magi/WebUI/package-lock.json* ./
RUN npm ci

COPY magi/WebUI ./
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
# The Python project (pyproject.toml at root, package at magi/) is
# copied as a unit. uv finds pyproject at the project root and
# installs deps into the venv. README.md is also copied because
# pyproject declares it under [project] readme.
COPY pyproject.toml uv.lock* README.md ./
COPY magi ./magi
RUN uv sync --frozen --no-dev --extra adam --extra eve

# -----------------------------------------------------------------------------
# Stage 3: runtime — Python + prebuilt SPA, run as non-root magi user
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime
RUN useradd --create-home --shell /bin/bash magi \
    && mkdir -p /workspace/state \
    && chown -R magi:magi /workspace

COPY --from=python-builder /opt/venv /opt/venv
COPY magi /app/magi
# Prebuilt SPA baked in. FastAPI's webui launcher mounts /app/magi/WebUI/dist
# as StaticFiles (with html=True) so /, /assets/* etc. all serve the SPA,
# and /api/* still hits the FastAPI routers (registered before the
# static mount so they take precedence).
COPY --from=web-builder /web/dist /app/magi/WebUI/dist

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MAGI_STATE_DIR=/workspace/state

WORKDIR /workspace
USER magi
VOLUME ["/workspace/state"]

EXPOSE 42069

# `magi --check` is role-aware (prints the resolved config for whichever
# role this container is playing) and exits 0 — safe liveness probe for
# both Adam and EVE until role-specific probes land in C3+.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD magi --check >/dev/null || exit 1

CMD ["magi"]
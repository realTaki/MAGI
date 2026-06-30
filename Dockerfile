# syntax=docker/dockerfile:1.7
# MAGI node — single image, role chosen at runtime via MAGI_NODE_ROLE.
#
# Whether the container plays Adam (enterprise scope, WebUI channel) or
# EVE (personal scope, Telegram channel) is decided by env, not by which
# image you pulled. Both roles use SQLite for local state; the file
# lives under /var/lib/magi/state and should be bind-mounted to the host
# in docker-compose so it persists across container restarts.

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /uvx /usr/local/bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app
COPY pyproject.toml uv.lock* ./
COPY magi ./magi

# Install full project (adam + eve extras) so the same image can serve
# either role. Phase 1 baselines; phase 4 may split if image size matters.
RUN uv sync --frozen --no-dev --extra adam --extra eve

# ---- runtime ----------------------------------------------------------------
FROM python:3.12-slim AS runtime
RUN useradd --create-home --shell /bin/bash magi \
    && mkdir -p /workspace/state \
    && chown -R magi:magi /workspace

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MAGI_STATE_DIR=/workspace/state

# /workspace is the container's working directory (matches the convention
# used by Agent / dev environments). SQLite lives under /workspace/state
# so a single bind mount at /workspace/state persists everything.
WORKDIR /workspace
USER magi
VOLUME ["/workspace/state"]

EXPOSE 69420

# `magi --check` is role-aware (prints the resolved config for whichever
# role this container is playing) and exits 0 — safe liveness probe for
# both Adam and EVE until role-specific probes land in C3+.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD magi --check >/dev/null || exit 1

CMD ["magi"]
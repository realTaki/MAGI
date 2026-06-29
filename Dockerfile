# syntax=docker/dockerfile:1.7
# MAGI node — single image, role chosen at runtime via MAGI_NODE_ROLE.
#
# Whether the container plays Adam (enterprise scope, WebUI channel,
# Postgres system of record) or EVE (personal scope, Telegram channel,
# local SQLite + Adam pull) is decided by env, not by which image you
# pulled. Build once, deploy everywhere.

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
    && mkdir -p /var/lib/magi/eve \
    && chown -R magi:magi /var/lib/magi

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MAGI_STATE_DIR=/var/lib/magi/eve

WORKDIR /app
USER magi
VOLUME ["/var/lib/magi/eve"]

# EVE does not bind a port in C0; Adam binds :8000 for the WebUI channel +
# RPC. Compose / k8s service definitions should expose :8000 only on the
# Adam service.
EXPOSE 8000 9100

# `magi --check` is role-aware (prints the resolved config for whichever
# role this container is playing) and exits 0 — safe liveness probe for
# both Adam and EVE until role-specific probes land in C3+.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD magi --check >/dev/null || exit 1

CMD ["magi"]
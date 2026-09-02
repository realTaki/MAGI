# syntax=docker/dockerfile:1.7
# One MAGI image for a MAGI runtime and its HTTP API.
# The operator UI lives in the sibling ``app/`` project and is not baked in.

FROM python:3.12-slim AS python-builder
COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /uvx /usr/local/bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app
COPY py-magi/pyproject.toml py-magi/uv.lock* py-magi/README.md ./
COPY py-magi ./
RUN uv sync --frozen --no-dev --extra adam --extra eva

FROM python:3.12-slim AS runtime
RUN useradd --create-home --shell /bin/bash magi \
    && mkdir -p /workspace/memories \
    && chown -R magi:magi /workspace

COPY --from=python-builder /opt/venv /opt/venv
COPY py-magi /app

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /workspace
USER magi

EXPOSE 42070

CMD ["magi", "node", "run", "--foreground"]

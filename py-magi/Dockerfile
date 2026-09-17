# syntax=docker/dockerfile:1.7
# One MAGI. Start: magi <handle> <base> <token>

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY py-magi /src
RUN uv sync --frozen --no-dev || uv sync --no-dev

FROM python:3.12-slim
RUN useradd --create-home --shell /bin/bash magi
COPY --from=builder /opt/venv /opt/venv
COPY py-magi /app
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER magi
WORKDIR /home/magi
ENTRYPOINT ["magi"]

# syntax=docker/dockerfile:1.7
# magi-asp: one ASP process. It starts MAGI pods; this image is not a MAGI.

FROM python:3.12-slim
WORKDIR /app
COPY magi-asp /app
RUN pip install --no-cache-dir fastapi "uvicorn[standard]>=0.32.0"
ENV PYTHONUNBUFFERED=1 \
    MAGI_ASP_HOST=0.0.0.0 \
    MAGI_ASP_PORT=42069
EXPOSE 42069
CMD ["python", "main.py"]

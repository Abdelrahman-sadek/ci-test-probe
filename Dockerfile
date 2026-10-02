# AUC Library Assistant — production image (non-root, pinned dependencies, healthcheck).
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    AGENTKIT_INDEX=/data/index.db AGENTKIT_LOG=/data/chat.jsonl
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-ara curl \
    && rm -rf /var/lib/apt/lists/* && useradd --create-home --uid 10001 agentkit
WORKDIR /app
COPY requirements.lock ./
RUN pip install --require-hashes -r requirements.lock
COPY pyproject.toml README.md ./
COPY agentkit ./agentkit
COPY plugins ./plugins
COPY knowledge ./knowledge
COPY evals ./evals
COPY config ./config
RUN pip install --no-deps . && mkdir -p /data && chown agentkit /data
USER agentkit
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1
# Build the seed index on first start, then serve.
CMD ["sh", "-c", "[ -f \"$AGENTKIT_INDEX\" ] || agentkit --index \"$AGENTKIT_INDEX\" ingest knowledge/auc-library/pages; exec agentkit --index \"$AGENTKIT_INDEX\" serve --host 0.0.0.0 --port 8000"]

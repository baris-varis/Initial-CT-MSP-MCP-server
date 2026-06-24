FROM python:3.11-slim

WORKDIR /app

# Install deps first (layer-cached on pyproject.toml changes)
COPY pyproject.toml .
RUN pip install --no-cache-dir \
    "mcp[cli]>=1.9.0" \
    "httpx>=0.27.0" \
    "pydantic>=2.0.0" \
    "python-dotenv>=1.0.0" \
    "tenacity>=8.2.0" \
    "uvicorn>=0.30.0" \
    "starlette>=0.40.0"

# Copy source (after deps to preserve cache)
COPY *.py ./

EXPOSE 8000

ENV PORT=8000 \
    CACHE_PATH=/tmp/ctgov.db \
    STATUS_TTL_DAYS=7 \
    META_TTL_DAYS=30 \
    CTGOV_RATE_INTERVAL=1.0 \
    CTGOV_TIMEOUT=30.0

CMD ["python", "server.py"]

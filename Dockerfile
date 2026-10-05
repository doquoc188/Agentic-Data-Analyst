FROM python:3.10.20-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    TRACE_ENABLED=false \
    TRACE_DIR=/app/runs

WORKDIR /app

# Install the existing Python dependencies; no Conda or frontend build.
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Keep code owned by root and give the application a writable trace directory.
RUN groupadd --gid 10001 app \
    && useradd --no-log-init --uid 10001 --gid app --create-home app \
    && chmod -R a+rX,go-w /app/app \
    && mkdir /app/runs \
    && chown app:app /app/runs

USER app

EXPOSE 8000

# Liveness only: this endpoint does not call Gemini or PostgreSQL.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + (os.environ.get('PORT') or '8000') + '/health', timeout=2).close()"]

# exec lets Uvicorn receive the container's stop signal directly.
CMD ["sh", "-c", "exec uvicorn app.api:app --host 0.0.0.0 --port \"${PORT:-8000}\""]

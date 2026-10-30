# KhetKavach: one container (FastAPI backend + static frontend). Works on Hugging Face Spaces (Docker),
# Render, Railway, Fly.io and Google Cloud Run. Listens on $PORT (default 7860, the HF Spaces port).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860 \
    KK_DB_PATH=/tmp/khetkavach/khetkavach.db

# Run as a non-root user (required on Hugging Face Spaces, good practice elsewhere).
RUN useradd --create-home --uid 1000 app
WORKDIR /home/app/khetkavach

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY --chown=app:app app ./app
COPY --chown=app:app scripts ./scripts
USER app

EXPOSE 7860
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"7860\")}/api/health', timeout=4)"
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]

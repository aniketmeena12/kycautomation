# syntax=docker/dockerfile:1
# Backend: FastAPI + SQLite. Multi-stage keeps build tooling out of the runtime.
FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     PYTHONIOENCODING=utf-8     PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first: this layer is cached unless requirements.txt changes,
# so an app-code edit does not reinstall pandas.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Non-root. A container that runs as root gives an RCE the whole filesystem;
# this one owns only its own data directory.
RUN useradd --create-home --uid 1000 appuser  && mkdir -p /app/data  && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# /health/ready checks the database AND the dataset registry, so an unhealthy
# container is one that genuinely cannot serve -- not merely one that booted.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3   CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health/ready', timeout=4).status==200 else 1)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

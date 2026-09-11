# syntax=docker/dockerfile:1

# Pin to a specific patch for reproducibility. Update as needed.
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install OS deps: curl for healthchecks; build-essential/pkg-config/libcairo2-dev
# are required to compile pycairo (a transitive dependency) from source on this
# slim base image — without them, `pip install` fails before the app ever builds.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        build-essential \
        pkg-config \
        libcairo2-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (better layer caching)
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

# Copy application code
COPY . /app

EXPOSE 8000

# Note: Running as root by default to avoid volume permission issues on Windows bind mounts.
# For production hardening, consider:
# RUN useradd -m -u 10001 appuser \
#     && chown -R appuser:appuser /app
# USER appuser

# Shell form (not the ["exec", "form"] array) so ${BACKEND_SERVER_WORKERS} is
# substituted at container start. Defaults to 2 workers if unset — matches this
# service's t3.small (2GB RAM) sizing; see backend/.env.example for reasoning.
CMD uvicorn src.main:backend_app --host 0.0.0.0 --port 8000 --workers ${BACKEND_SERVER_WORKERS:-2}

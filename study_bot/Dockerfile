# ---------- build ----------
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=UTC

WORKDIR /app

# System dependencies (tzdata for reliable IANA timezones).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source.
COPY . .

# Ensure runtime directories exist.
RUN mkdir -p data/exports logs

# Liveness probe: confirms the SQLite DB was created and is queryable.
HEALTHCHECK --interval=1m --timeout=10s --start-period=30s --retries=3 \
    CMD python healthcheck.py || exit 1

# Run the bot with long polling.
CMD ["python", "bot.py"]

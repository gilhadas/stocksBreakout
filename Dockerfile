# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.14.2
FROM python:${PYTHON_VERSION}-slim AS base

# Prevents Python from writing pyc files.
ENV PYTHONDONTWRITEBYTECODE=1

# Keeps Python from buffering stdout and stderr.
ENV PYTHONUNBUFFERED=1

# All cron jobs run on US Eastern time.
ENV TZ=America/New_York

WORKDIR /app

# ── System dependencies ────────────────────────────────────────────────────────
# curl   → healthchecks.io pings in cron jobs
# tzdata → proper TZ=America/New_York support
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

# ── Supercronic ────────────────────────────────────────────────────────────────
# Docker-friendly cron: no root required, logs to stdout, inherits env vars.
# Detects CPU arch automatically (amd64 / arm64).
ARG SUPERCRONIC_VERSION=v0.2.33
RUN ARCH=$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/') && \
    curl -fsSL \
      "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${ARCH}" \
      -o /usr/local/bin/supercronic && \
    chmod +x /usr/local/bin/supercronic

# ── Non-privileged user ────────────────────────────────────────────────────────
# Needs a real home dir and login shell so cron jobs execute correctly.
ARG UID=10001
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home "/home/appuser" \
    --shell "/bin/sh" \
    --uid "${UID}" \
    appuser

# ── Python dependencies ────────────────────────────────────────────────────────
# Installed globally (no venv needed in Docker).
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=bind,source=requirements.txt,target=requirements.txt \
    python -m pip install -r requirements.txt

# ── Application source ────────────────────────────────────────────────────────
# --chown ensures appuser can write to scanner_output/ at runtime.
COPY --chown=appuser:appuser . .

# Create output directories (populated on first run; mount as a volume for persistence).
RUN mkdir -p \
        scanner_output/logs \
        scanner_output/signals \
        scanner_output/exits \
        scanner_output/rejections \
        scanner_output/backtests \
    && chown -R appuser:appuser scanner_output input

# ── Entrypoint ─────────────────────────────────────────────────────────────────
COPY --chown=appuser:appuser docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# ── Runtime ────────────────────────────────────────────────────────────────────
USER appuser

# Expose Streamlit / webhook server port (only needed when running app.py or webhook_server.py).
EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]

# Default: run all scheduled cron jobs.
# Override to run the scanner directly:
#   docker run --env-file .env scanner python3 breakout_scanner.py input/ALL.txt --mode swing --mock
CMD ["supercronic", "/app/docker/crontab"]

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
#
# torch FIRST, from PyTorch's CPU-only index. requirements.txt asks for plain
# `torch>=2.0.0`, and on linux/x86_64 the default PyPI wheel is the CUDA build —
# nvidia/* 2.7 GB + triton 691 MB + torch 1.2 GB = ~4.6 GB of GPU runtime on a
# GPU-less t3.small. FinBERT is the only torch consumer and pins device=-1
# (CPU) in quantkit/sentiment/finbert.py, so none of it is ever used. It filled
# the 29 GB disk and failed three deploys with "no space left on device"
# (2026-07-23). CPU build is the same version, ~350 MB.
#
# Deliberately a SEPARATE step with a scoped --index-url, not --extra-index-url
# on requirements.txt: with two indexes both serving `torch`, pip picks by
# version and the CUDA build can win. Installing it first makes requirements.txt
# a no-op for torch (>=2.0.0 already satisfied) and the choice deterministic.
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --index-url https://download.pytorch.org/whl/cpu torch

RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=bind,source=requirements.txt,target=requirements.txt \
    python -m pip install -r requirements.txt

# ── Application source ────────────────────────────────────────────────────────
# --chown ensures appuser can write to scanner_output/ at runtime.
COPY --chown=appuser:appuser . .

# Create output + input directories (populated on first run; mount as a volume for
# persistence). input/ is created here, not just chowned, because *.txt watchlists
# are gitignored — a fresh clone has no input/ directory at all.
RUN mkdir -p \
        scanner_output/logs \
        scanner_output/signals \
        scanner_output/exits \
        scanner_output/rejections \
        scanner_output/backtests \
        input \
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
# IMPORTANT: .env is excluded from the image (see .dockerignore). Pass credentials at runtime:
#   docker run --env-file .env scanner supercronic /app/docker/crontab
# Required env vars for email notifications: GMAIL_APP_PASSWORD, NOTIFY_RECIPIENTS
# Required for Telegram: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
# Override to run the scanner directly:
#   docker run --env-file .env scanner python3 breakout_scanner.py input/ALL.txt --mode swing --mock
CMD ["supercronic", "/app/docker/crontab"]

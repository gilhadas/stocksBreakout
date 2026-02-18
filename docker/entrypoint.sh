#!/bin/sh
set -e

# Load .env if present (useful when running without docker compose --env-file).
# Supercronic inherits all Docker ENV vars automatically, so this is only a
# convenience fallback for direct `docker run` without --env-file.
if [ -f /app/.env ]; then
    set -a
    # shellcheck disable=SC1091
    . /app/.env
    set +a
fi

exec "$@"

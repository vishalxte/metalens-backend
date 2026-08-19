#!/usr/bin/env bash
#
# Container entrypoint.
#
# Three jobs, in order:
#   1. Prove the compiled module is actually loadable, before anything
#      else. A bad .so otherwise surfaces as a confusing uvicorn
#      traceback several seconds later.
#   2. Wait for the PostgreSQL running on the WINDOWS HOST to answer.
#      The database is not part of this compose stack, so nothing else
#      orders startup against it — without this, the first request after
#      `docker compose up` can hit a connection refused.
#   3. Optionally run alembic migrations, then exec the real command.
#
# Everything is opt-out via environment variables so the same image can
# be started in a degraded mode when you are debugging.

set -euo pipefail

log() { printf '[entrypoint] %s\n' "$*"; }
die() { printf '[entrypoint] ERROR: %s\n' "$*" >&2; exit 1; }

DB_HOST="${POSTGRES_HOST:-host.docker.internal}"
DB_PORT="${POSTGRES_PORT:-5432}"
DB_USER="${POSTGRES_USER:-postgres}"
DB_NAME="${POSTGRES_DB:-postgres}"

WAIT_FOR_DB="${WAIT_FOR_DB:-true}"
WAIT_FOR_DB_TIMEOUT="${WAIT_FOR_DB_TIMEOUT:-60}"
RUN_MIGRATIONS="${RUN_MIGRATIONS:-true}"


# ── 1. the compiled module loads ──────────────────────────────────────
if ! python -c "import app, sys; sys.stdout.write(app.__name__)" >/dev/null 2>&1; then
    log "Compiled application module could not be imported. Contents of /app:"
    ls -la /app >&2
    die "app.cpython-*.so is missing or was built for a different Python/platform.
       Rebuild with:  docker compose build --no-cache"
fi
log "Compiled module app.cpython-*.so loaded OK"


# ── 2. host PostgreSQL is reachable ───────────────────────────────────
if [ "${WAIT_FOR_DB}" = "true" ]; then
    log "Waiting for PostgreSQL at ${DB_HOST}:${DB_PORT} (up to ${WAIT_FOR_DB_TIMEOUT}s)..."

    deadline=$(( SECONDS + WAIT_FOR_DB_TIMEOUT ))
    until pg_isready -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -q; do
        if [ "${SECONDS}" -ge "${deadline}" ]; then
            die "PostgreSQL at ${DB_HOST}:${DB_PORT} did not become ready.

       The database runs on the Windows host, NOT in Docker, so the
       container reaches it over the host gateway. Check, on Windows:

         1. postgresql.conf   listen_addresses = '*'
         2. pg_hba.conf       host all all 172.16.0.0/12 scram-sha-256
         3. restart the PostgreSQL service after editing those files
         4. Windows Firewall allows inbound TCP ${DB_PORT}

       Then:  docker compose up -d --force-recreate"
        fi
        sleep 2
    done

    log "PostgreSQL is ready"
fi


# ── 3. migrations, then hand over ─────────────────────────────────────
if [ "${RUN_MIGRATIONS}" = "true" ]; then
    log "Running alembic upgrade head"
    alembic upgrade head
    log "Migrations up to date"
else
    log "RUN_MIGRATIONS=false — skipping alembic"
fi

log "Starting: $*"
exec "$@"

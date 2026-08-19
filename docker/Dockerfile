# Nuitka build + runtime image for the Metalens backend.
#
# NOTE: there is deliberately no `# syntax=docker/dockerfile:...` line.
# That directive makes Docker pull a separate frontend image from Docker
# Hub before it can even parse this file, which fails on a locked-down
# or offline network and gives a confusing error. Everything used here
# (multi-stage, ARG before FROM, COPY --from, FROM scratch) is supported
# by the built-in frontend, so the extra pull buys nothing.
#
# WHAT COMES OUT OF THIS FILE
# ───────────────────────────
# The whole app/ package is compiled by Nuitka into ONE extension
# module — app.cpython-311-x86_64-linux-gnu.so — and the runtime image
# ships that .so with no .py source of the application anywhere on disk.
#
# Four stages, each with one job:
#
#   deps     installs the third-party wheels into /opt/venv. Shared by
#            the builder and the runtime, so runtime dependencies are
#            resolved exactly once and can never drift between the
#            machine that compiled the .so and the machine that loads it.
#   builder  adds Nuitka + a C toolchain and compiles app/ -> /out.
#   export   FROM scratch, holds only the .so. Used with
#            `docker build --target export --output type=local,dest=build`
#            to drop the .so onto the Windows disk.
#   runtime  final image: /opt/venv + the .so + alembic. No app/*.py.
#
# WHY THE PYTHON VERSION IS PINNED IN TWO PLACES
# ──────────────────────────────────────────────
# A Nuitka module .so is bound to one CPython minor version and one
# platform — the filename says so. It is compiled by `deps`/`builder` on
# python:3.11 and loaded by `runtime` on python:3.11. Bump PYTHON_VERSION
# and both move together; that is the only safe way to change it.
# It also means this .so runs on Linux x86_64 only — it cannot be
# imported by the Python 3.14 installed on the Windows host.

ARG PYTHON_VERSION=3.11
ARG PG_MAJOR=17


# ──────────────────────────────────────────────────────────────────────
# Stage 1: deps — third-party packages, installed once
# ──────────────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim-bookworm AS deps

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

# build-essential is needed here (not only in the builder stage) because
# a few pinned packages may fall back to an sdist on this platform.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /src

COPY requirements.txt ./
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
 && /opt/venv/bin/pip install -r requirements.txt

ENV PATH="/opt/venv/bin:${PATH}"


# ──────────────────────────────────────────────────────────────────────
# Stage 2: builder — Nuitka compiles app/ into a single .so
# ──────────────────────────────────────────────────────────────────────
FROM deps AS builder

COPY requirements-build.txt ./
RUN pip install -r requirements-build.txt

# Only the application package is copied. Nothing else is needed to
# compile it, and a smaller context means a faster, more cacheable build.
COPY app ./app

# --module            : produce an importable extension module, not a binary.
# --include-package   : pull in every submodule of app/ (api, services,
#                       repositories, models, ...) so they all land in the
#                       one .so instead of being looked for as .py at runtime.
# --remove-output     : delete the multi-hundred-MB C build tree afterwards.
# --no-pyi-file       : the .pyi stub would leak the module's public API.
RUN python -m nuitka \
        --module app \
        --include-package=app \
        --output-dir=/out \
        --remove-output \
        --no-pyi-file \
        --assume-yes-for-downloads \
        --jobs="$(nproc)" \
 && rm -f /out/*.pyi \
 && ls -la /out


# ──────────────────────────────────────────────────────────────────────
# Stage 3: export — nothing but the .so, for copying to the host
# ──────────────────────────────────────────────────────────────────────
FROM scratch AS export
COPY --from=builder /out/ /


# ──────────────────────────────────────────────────────────────────────
# Stage 4: runtime — what actually serves traffic
# ──────────────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ARG PG_MAJOR

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH=/app \
    PROJECT_ROOT=/app

# postgresql-client supplies pg_dump for app/backup/backupscheduler.py.
# It comes from the PGDG repo rather than Debian's, because Debian
# bookworm ships client 15 and pg_dump refuses to dump a NEWER server —
# a 15 client against a 16/17 host database fails with a version
# mismatch. A newer client dumps older servers fine, so PG_MAJOR should
# be >= the version of PostgreSQL running on the Windows host.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        libpq5 curl ca-certificates gnupg \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-${PG_MAJOR} \
 && apt-get purge -y --auto-remove curl gnupg \
 && rm -rf /var/lib/apt/lists/*

COPY --from=deps /opt/venv /opt/venv

WORKDIR /app

# THE application: one file. No app/*.py is copied here, by design —
# that is the whole point of the exercise.
COPY --from=builder /out/app.cpython-*-linux-gnu.so /app/

# Alembic stays plain Python, as requested. It imports app.database.base,
# which resolves to the compiled module above, so `alembic upgrade head`
# works unchanged inside the container.
COPY alembic ./alembic
COPY alembic.ini ./

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

# The sed is not cosmetic: this repo lives on a Windows disk, and an
# editor or `git config core.autocrlf=true` will silently give the script
# CRLF endings. The kernel then reads the shebang as "/usr/bin/env bash\r"
# and the container dies with "exec format error" / "no such file or
# directory" — the single most common Windows-to-Docker failure.
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh \
 && chmod +x /usr/local/bin/entrypoint.sh \
 && mkdir -p /app/logs /app/uploads

# Deliberately left running as root.
#
# /app/uploads and /app/logs are bind-mounted from the Windows disk. A
# bind mount keeps the HOST's ownership, not the image's, so a non-root
# USER here writes fine on Docker Desktop for Windows but fails with
# PermissionError the moment the same compose file is used on a Linux
# host or a WSL2 path. Since this stack is a single self-contained
# service with no inbound shell, root inside the container buys
# predictability at very little cost. To harden it later, add
#   RUN useradd --uid 10001 appuser && chown -R appuser /app
#   USER appuser
# and make sure the mounted host folders are owned by uid 10001.

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4).status == 200 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

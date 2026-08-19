# Backend → single `.so` (Nuitka) → Docker

Sagla `app/` package Nuitka ne **ek `.so` file** madhe compile hoto, ani to
`.so` Docker (Linux) container madhun chalto. PostgreSQL tumchya **Windows
machine varach** rahto — Docker madhe nahi. Redis kuthech nahi. Alembic
plain Python chach rahto.

---

## TL;DR — 3 commands

```bat
cd D:\new\chatbot_new_1\metalens_web_app_4\backend

build.bat                 :: 1. app\ -> build\app.cpython-311-x86_64-linux-gnu.so + image
docker compose up -d      :: 2. chalu kara
start http://localhost:8000/docs
```

Code badalla ki punha `build.bat` ani `docker compose up -d --build`.

Pahilyanda chalvnyachya adhi ek vela **"Host PostgreSQL setup"** section
kara — to na kelyas container database la connect hou shakat nahi.

---

## What you get

| | |
|---|---|
| Compiled artifact | `build\app.cpython-311-x86_64-linux-gnu.so` (~3.1 MB) |
| Runtime image | `metalens-backend:nuitka` |
| App source in the image | **none** — only the `.so` |
| Database | PostgreSQL on the Windows host, over `host.docker.internal` |
| Redis | not used anywhere; both caches are PostgreSQL tables |
| Alembic | plain `.py`, runs inside the container against the `.so` |
| Build time | ~60 s for the Nuitka step on 2 cores, cached after that |

---

## New and changed files

**New**

```
docker/Dockerfile              4-stage build: deps -> builder -> export -> runtime
docker/entrypoint.sh           checks the .so, waits for the host DB, runs alembic
docker-compose.yml             the single `api` service
.env.docker                    container-side settings (POSTGRES_HOST etc.)
.dockerignore                  keeps venv\ and .env out of the build context
.gitattributes                 forces LF on the shell scripts
requirements-build.txt         nuitka==2.7.12, build-time only
build.bat / build.sh           one command: compile + image + verify
DOCKER_NUITKA_README.md        this file
app\**\__init__.py             12 files — see "Why __init__.py" below
```

**Changed** — four small fixes, each explained inline in the code:

| File | Why |
|---|---|
| `app\services\otp_service.py` | `load_dotenv()` walks the caller's stack frames; inside a compiled module there is no such frame and the app died at import with a bare `AssertionError`. Now `load_dotenv(find_dotenv(usecwd=True))`. |
| `app\backup\backupscheduler.py` | `PROJECT_ROOT` was `Path(__file__).parents[2]`. `__file__` no longer points at the source tree once compiled, so relative `BACKUP_DIRS` resolved to the wrong place. Now `PROJECT_ROOT` env var → `__file__` (source mode only) → cwd. |
| `app\api\dependencies\auth.py` + `app\models\audit_log.py` | **Pre-existing bug, not caused by Nuitka.** Five sites used `AuditStatus.DENIED`, which migration `d9e0f1a2b3c4` had removed. Every 403 gate therefore raised `AttributeError` and returned **500 instead of 403**. Now `AuditStatus.FAILURE`. |
| `app\api\v1\sessions.py` | **Pre-existing bug, not caused by Nuitka.** `UserSessionResponse.model_validate(row)` read `row.is_active` — a *method* — into a `bool` field, so `GET /audit/sessions`, `/audit/sessions/active` and `/audit/sessions/{id}` all returned **500**. Now the columns are copied explicitly and `row.is_active()` is called. |

Nothing else in `app/` was touched. `.env`, `alembic\`, `alembic.ini` and
`requirements.txt` are unchanged.

### Why `__init__.py`

Only `app\models` and `app\backup` had one; the rest were implicit
namespace packages. `nuitka --module app --include-package=app` walks a
*real* package to discover the submodules it folds into the `.so`, and a
namespace package gives it nothing to start from. The 12 new files are
comments only — they add no behaviour, and running from source on
Windows works exactly as before.

---

## Host PostgreSQL setup (one time)

The container is a separate machine on a separate network. `localhost`
inside it means the container, so it reaches your Windows PostgreSQL
through `host.docker.internal` — and PostgreSQL has to be willing to
accept that connection.

**1. `postgresql.conf`** (e.g. `C:\Program Files\PostgreSQL\17\data\postgresql.conf`)

```conf
listen_addresses = '*'
```

**2. `pg_hba.conf`** (same folder) — add at the end:

```conf
# Docker Desktop containers
host    all    all    172.16.0.0/12    scram-sha-256
```

**3. Restart the service:**

```bat
net stop postgresql-x64-17 && net start postgresql-x64-17
```

**4. Windows Firewall** — allow inbound TCP 5432 (Private profile is
enough for Docker Desktop).

**5. The `vector` extension must exist in the database:**

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

Quick check from inside the container:

```bat
docker compose run --rm --entrypoint sh api -c "pg_isready -h host.docker.internal -p 5432 -U postgres"
```

---

## Build

```bat
build.bat            :: normal build (uses the Docker layer cache)
build.bat clean      :: --no-cache, rebuild everything
```

`build.bat` does three things:

1. `docker build --target export --output type=local,dest=build .`
   Compiles `app\` with Nuitka inside the container and writes the
   resulting `.so` **onto this disk**, into `build\`.
2. `docker compose build` — builds `metalens-backend:nuitka`.
3. Prints `/app` from inside the image and confirms no application
   `.py` is left in it.

Git Bash / WSL / Linux / macOS: `./build.sh` (same steps).

If you only want the `.so` and not the image:

```bat
docker build -f docker\Dockerfile --target export --output type=local,dest=build .
```

---

## Run

```bat
docker compose up -d          :: start
docker compose logs -f api    :: follow logs
docker compose restart api
docker compose down           :: stop
docker compose up -d --build  :: rebuild + restart after a code change
```

* API: <http://localhost:8000>
* Swagger: <http://localhost:8000/docs>

On startup the entrypoint prints, in order: `Compiled module ... loaded
OK`, `PostgreSQL is ready`, `Migrations up to date`, then uvicorn's
banner. If it stops at any of those, the message says what to fix.

### Environment switches

Set on the `api` service in `docker-compose.yml`:

| Variable | Default | Effect |
|---|---|---|
| `RUN_MIGRATIONS` | `true` | run `alembic upgrade head` before starting |
| `WAIT_FOR_DB` | `true` | block until the host PostgreSQL answers |
| `WAIT_FOR_DB_TIMEOUT` | `60` | seconds before giving up |
| `PROJECT_ROOT` | `/app` | base for relative `BACKUP_DIRS` |

Migrations by hand:

```bat
docker compose run --rm -e RUN_MIGRATIONS=false api alembic upgrade head
docker compose run --rm -e RUN_MIGRATIONS=false api alembic history
```

### Mounted folders

| Windows | Container | |
|---|---|---|
| `.\uploads` | `/app/uploads` | uploaded documents |
| `.\logs` | `/app/logs` | rotating `app.log` |
| `.\backups` | `/backups` | `BACKUP_ROOT` |

---

## `.env` vs `.env.docker`

`.env` is untouched and is still what you use when running from source
on Windows. `.env.docker` is what the container reads. Only four values
differ, and each has to:

| | `.env` | `.env.docker` | why |
|---|---|---|---|
| `POSTGRES_HOST` | `localhost` | `host.docker.internal` | `localhost` in the container is the container |
| `BACKUP_ROOT` | `D:/backups/metalens` | `/backups` | `D:/...` is not an absolute POSIX path, so `backupscheduler` refuses it |
| `BACKUP_ON_STARTUP` | `true` | `false` | otherwise every `docker compose up` fires a `pg_dump` |
| `PG_DUMP_PATH` | `pg_dump` | `pg_dump` | in the image it is on `PATH` (installed from the PGDG repo) |

**Keep them in sync.** If you change a secret in `.env`, change it in
`.env.docker` too. Neither file is copied into the image — `.dockerignore`
excludes both, and compose injects `.env.docker` at container start — so
the OpenAI key and the SMTP password never land in an image layer.

### pg_dump version

`PG_MAJOR` in `docker-compose.yml` (default `17`) is the PostgreSQL
client version installed in the image. `pg_dump` **cannot dump a newer
server than itself**, so set this to your Windows PostgreSQL major
version or higher, then rebuild.

---

## Verifying the source really is gone

```bat
docker run --rm --entrypoint sh metalens-backend:nuitka -c "ls -la /app"
```

You should see `app.cpython-311-x86_64-linux-gnu.so`, `alembic/`,
`alembic.ini`, `logs/`, `uploads/` — and no `app/` directory.

```bat
docker run --rm --entrypoint sh metalens-backend:nuitka -c "find / -name '*.py' -path '*/app/*' 2>/dev/null"
```

Returns nothing. `build.bat` runs this check for you as step 3.

---

## Things worth knowing

**The `.so` is Linux + x86_64 + CPython 3.11 only.** The filename says
so. Your Windows Python 3.14 cannot import it, and neither can a 3.12
container. That is why `PYTHON_VERSION` is a single build arg feeding
both the stage that compiles it and the stage that loads it — change it
in one place and they move together.

**`uvicorn --reload` will not work** against the compiled module; there
is no `.py` for the watcher to watch. Develop from source on Windows,
compile when you ship.

**Tracebacks stay readable.** Compiled frames still report a file and a
line number (`/app/app/api/v1/sessions.py, line 73`) even though that
path does not exist on disk — the line numbers match your source.

**Rebuild after every source change.** The `.so` is a build artifact. If
the container seems to ignore an edit, you skipped `build.bat`.

**Runs as root inside the container**, deliberately — bind mounts keep
the host's ownership, so a non-root user breaks the moment this compose
file is used on Linux or a WSL2 path. `docker/Dockerfile` has the two
lines to change if you want to harden it.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `PostgreSQL at host.docker.internal:5432 did not become ready` | "Host PostgreSQL setup" above — usually `listen_addresses` or `pg_hba.conf` |
| `password authentication failed` | `POSTGRES_PASSWORD` in `.env.docker` doesn't match the host DB |
| `type "vector" does not exist` | `CREATE EXTENSION vector;` in the database on the host |
| `exec /usr/local/bin/entrypoint.sh: no such file or directory` | CRLF in `entrypoint.sh`. `.gitattributes` + the `sed` in the Dockerfile handle it; if you edited it in Notepad, save as LF and rebuild |
| `Compiled application module could not be imported` | `.so` missing or built for another Python. `build.bat clean` |
| `pg_dump: server version X, pg_dump version 17` | raise `PG_MAJOR` in `docker-compose.yml` and rebuild |
| `--output type=local` not recognised | BuildKit off. `set DOCKER_BUILDKIT=1` (build.bat already does) |
| Port 8000 in use | change the left side of `"8000:8000"` in `docker-compose.yml` |
| Nuitka step is slow | first build only; the `deps` layer is cached afterwards |

---

## How it was verified

On Linux / CPython 3.11 with a real PostgreSQL 16 + pgvector, using the
compiled `.so` and no application `.py` present:

* `import app` and `from app.main import app` — OK
* `alembic upgrade head` — all 21 migrations applied
* OpenAPI generated: **39 paths, 42 schemas** (`/docs` renders)
* auth: JWT issue/verify, bcrypt hashing, 401 on bad credentials,
  403 on every role gate, 422 on invalid bodies
* reads: `/users/me`, `/audit/logs`, `/audit/logs/{id}`, `/audit/summary`,
  `/audit/metadata`, `/audit/export` (CSV), `/audit/sessions`,
  `/audit/sessions/active`, `/activity/logs`, `/activity/metadata`,
  `/documents`, `/conversations`, `/customers`
* writes: `POST /customers` (201, cascade user creation), `POST
  /activity/event`, `POST /audit/sessions/expire-stale`
* audit rows written correctly (`CUSTOMER_CREATED`, `USER_CREATED`,
  `PERMISSION_DENIED / FAILURE`)
* background work: APScheduler OTP job, backup thread, `pg_dump` backup
  completed and pruned
* **0 HTTP 500s across the whole sweep**

The Docker *image* build itself could not be executed in the environment
these files were written in (no Docker Hub access there), but the
compilation, the import, the migrations and every runtime path above are
exactly what the Dockerfile performs, run for real.

"""
Scheduled backup: pg_dump of the database plus a copy of the configured
project folders.

Public API is unchanged — `backup()` and `start_scheduler()`, called the
same way from app/main.py. What changed is that nothing is hardcoded any
more and nothing here can take the application down.

WHY THE PATHS ARE NOT CONSTANTS ANY MORE
────────────────────────────────────────
This module previously carried `/home/govind/Gautam-dev/` and a matching
uploads path from a different machine. On Windows those simply do not
exist, so the very first thing backup() did — iterate BACKUP_ROOT to
delete old folders — raised FileNotFoundError. It ran inside a daemon
thread, so the app kept serving and the failure was invisible.

Now:
  - BACKUP_ROOT comes from settings and must be configured explicitly.
  - The folders to copy are RELATIVE to the project root and resolved at
    runtime, so moving or deploying the project doesn't break them. An
    absolute path in config would recreate the original bug.

SAFETY
──────
backup() deletes previous `backup_*` folders before writing a new one.
A mis-set BACKUP_ROOT therefore means deleting in the wrong place, so
_resolve_backup_root() refuses to proceed unless the path is configured,
absolute, and outside the project tree. Only directories whose name
starts with "backup_" are ever removed.
"""
import os
import shutil
import subprocess
import time

from datetime import datetime
from pathlib import Path

import schedule

from app.core.config import settings
from app.core.logging import logger


# Project root = the directory containing app/.
#
# WHY THIS IS NOT JUST Path(__file__).parents[2] ANY MORE
# ───────────────────────────────────────────────────────
# When this package is compiled by Nuitka into a single extension module
# (app.cpython-*.so), every module inside it reports the .so file itself
# as __file__ — e.g. /app/app.cpython-311-x86_64-linux-gnu.so — not
# <root>/app/backup/backupscheduler.py. Walking three parents up from
# that lands somewhere arbitrary (/, on the Docker image), which would
# make every relative BACKUP_DIRS entry resolve to the wrong place.
#
# Resolution order, first match wins:
#   1. PROJECT_ROOT env var — what the Docker image sets. Explicit and
#      correct in both compiled and source mode.
#   2. __file__ arithmetic — only trusted when __file__ really is this
#      module's .py, i.e. when running from plain source.
#   3. Current working directory — the process is started from the
#      project root in every documented setup.
def _resolve_project_root() -> Path:
    env_root = (os.getenv("PROJECT_ROOT") or "").strip()
    if env_root:
        return Path(env_root).resolve()

    try:
        here = Path(__file__).resolve()
        # Source mode: .../<root>/app/backup/backupscheduler.py
        if here.suffix == ".py" and here.parent.name == "backup":
            return here.parents[2]
    except (NameError, OSError, IndexError):
        pass

    return Path.cwd().resolve()


PROJECT_ROOT = _resolve_project_root()

BACKUP_DIR_PREFIX = "backup_"


class BackupConfigError(Exception):
    """Backup is misconfigured. Raised before anything is touched."""


def _resolve_backup_root() -> Path:
    """
    Validates BACKUP_ROOT before any destructive operation.

    Three checks, all of them guarding the rmtree in _prune_old_backups:

      1. Configured at all — an empty value would otherwise resolve to
         the current working directory.
      2. Absolute — a relative path depends on where uvicorn was
         started from, so the same config could point at different
         places on different runs.
      3. Outside the project tree — backing the project up into itself
         grows without bound, and the delete step would be pointed at
         source code.
    """
    raw = (settings.BACKUP_ROOT or "").strip()

    if not raw:
        raise BackupConfigError(
            "BACKUP_ROOT is not set. Add an absolute path to .env, "
            "e.g. BACKUP_ROOT=D:/backups/metalens"
        )

    root = Path(raw).expanduser()

    if not root.is_absolute():
        raise BackupConfigError(
            f"BACKUP_ROOT must be an absolute path, got {raw!r}"
        )

    root = root.resolve()

    if root == PROJECT_ROOT or PROJECT_ROOT in root.parents:
        raise BackupConfigError(
            f"BACKUP_ROOT ({root}) is inside the project ({PROJECT_ROOT}). "
            "Choose a location outside the project."
        )

    return root


def _resolve_source_dirs() -> list:
    """
    Turns the comma-separated BACKUP_DIRS setting into real paths under
    the project root. Missing entries are skipped with a warning rather
    than aborting — one folder that hasn't been created yet (uploads on
    a fresh checkout) should not cost you the database dump.
    """
    resolved = []

    for entry in (settings.BACKUP_DIRS or "").split(","):
        name = entry.strip()

        if not name:
            continue

        # Relative entries resolve under the project root; an absolute
        # entry is honoured as-is for the occasional external folder.
        candidate = Path(name)
        path = candidate if candidate.is_absolute() else (PROJECT_ROOT / candidate)

        if path.exists():
            resolved.append(path)
        else:
            logger.warning(
                f"Backup source folder does not exist, skipping: {path}",
                extra={"event": "backup_source_missing", "path": str(path)}
            )

    return resolved


def _prune_old_backups(root: Path) -> int:
    """
    Removes old backup folders, keeping the newest settings.BACKUP_KEEP.

    Only directories named `backup_*` directly under an already-validated
    root are considered — anything else in that folder is left alone.
    """
    existing = sorted(
        (p for p in root.iterdir()
         if p.is_dir() and p.name.startswith(BACKUP_DIR_PREFIX)),
        key=lambda p: p.name,
        reverse=True
    )

    removed = 0

    for old in existing[max(settings.BACKUP_KEEP, 0):]:
        try:
            shutil.rmtree(old)
            removed += 1
        except OSError as exc:
            # A locked file shouldn't stop the new backup being written.
            logger.warning(
                f"Could not remove old backup {old}: {exc}",
                extra={"event": "backup_prune_failed", "path": str(old)}
            )

    return removed


def _new_backup_dir(root: Path) -> Path:
    """
    Creates a uniquely-named backup folder.

    The timestamp is only second-resolution, so two runs inside the same
    second produce the same name — the startup backup plus a manually
    triggered one, for instance. With mkdir(exist_ok=True) the second run
    would silently write into the first one's folder, mixing two backups
    together and leaving one of them incomplete.

    A `_2`, `_3`, ... suffix is appended only when that actually happens,
    so normal folder names stay clean. exist_ok=False makes the creation
    itself the collision check, avoiding a check-then-create race between
    the scheduler thread and any other caller.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    attempt = 1

    while True:
        suffix = "" if attempt == 1 else f"_{attempt}"
        candidate = root / f"{BACKUP_DIR_PREFIX}{timestamp}{suffix}"

        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
        except FileExistsError:
            attempt += 1


def _dump_database(destination: Path) -> None:
    """
    pg_dump into `destination`.

    Written in BINARY mode deliberately. Text mode on Windows translates
    "\\n" to "\\r\\n", which corrupts a dump so that restoring it fails —
    a bug you only discover when you actually need the backup.
    """
    env = os.environ.copy()
    env["PGPASSWORD"] = settings.POSTGRES_PASSWORD

    with open(destination, "wb") as output:
        subprocess.run(
            [
                settings.PG_DUMP_PATH,
                "-h", settings.POSTGRES_HOST,
                "-p", str(settings.POSTGRES_PORT),
                "-U", settings.POSTGRES_USER,
                "-d", settings.POSTGRES_DB,
            ],
            stdout=output,
            stderr=subprocess.PIPE,
            env=env,          # was missing before, so PGPASSWORD never
                              # reached pg_dump and it prompted/failed
            check=True
        )


def backup() -> bool:
    """
    Runs one backup. Returns whether it succeeded.

    Never raises: it is called from a daemon thread where an exception
    would silently kill the scheduler loop and stop all future backups.
    """
    started = time.time()

    try:
        root = _resolve_backup_root()
    except BackupConfigError as exc:
        logger.error(
            f"Backup skipped — {exc}",
            extra={"event": "backup_misconfigured"}
        )
        return False

    try:
        root.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"Backup started — destination root {root}",
            extra={"event": "backup_started", "backup_root": str(root)}
        )

        pruned = _prune_old_backups(root)

        backup_dir = _new_backup_dir(root)

        # --- database ---
        _dump_database(backup_dir / "database.sql")
        logger.info("Backup: database dump completed")

        # --- project folders ---
        copied = []

        for source in _resolve_source_dirs():
            shutil.copytree(source, backup_dir / source.name, dirs_exist_ok=True)
            copied.append(source.name)

        elapsed = round(time.time() - started, 2)

        logger.info(
            f"Backup completed in {elapsed}s — {backup_dir} "
            f"(folders: {copied or 'none'}, pruned: {pruned})",
            extra={
                "event": "backup_completed",
                "backup_dir": str(backup_dir),
                "folders": copied,
                "pruned": pruned,
                "duration_sec": elapsed
            }
        )
        return True

    except FileNotFoundError:
        # Raised by subprocess when the pg_dump executable isn't found —
        # by far the most common Windows failure.
        logger.error(
            f"Backup failed — pg_dump not found at {settings.PG_DUMP_PATH!r}. "
            "Set PG_DUMP_PATH in .env to the full path, e.g. "
            "C:/Program Files/PostgreSQL/16/bin/pg_dump.exe",
            extra={"event": "backup_failed", "reason": "pg_dump_not_found"}
        )
        return False

    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode("utf-8", "replace").strip()
        logger.error(
            f"Backup failed — pg_dump exited {exc.returncode}: {stderr}",
            extra={"event": "backup_failed", "reason": "pg_dump_error"}
        )
        return False

    except Exception:
        logger.error(
            "Backup failed unexpectedly",
            exc_info=True,
            extra={"event": "backup_failed", "reason": "unexpected"}
        )
        return False


def start_scheduler() -> None:
    """
    Scheduler loop. Runs in the daemon thread started by app/main.py.

    Returns immediately when backups are disabled or misconfigured,
    instead of spinning a thread that can only ever log failures.

    NOTE for app/main.py: this is imported there under an alias
    (`start_backup_scheduler`) because that module also defines an
    `async def start_scheduler` for the OTP scheduler, which would
    otherwise rebind this name.
    """
    if not settings.BACKUP_ENABLED:
        logger.info(
            "Backup scheduler not started — BACKUP_ENABLED is false",
            extra={"event": "backup_scheduler_disabled"}
        )
        return

    try:
        root = _resolve_backup_root()
    except BackupConfigError as exc:
        logger.error(
            f"Backup scheduler not started — {exc}",
            extra={"event": "backup_scheduler_disabled"}
        )
        return

    if settings.BACKUP_ON_STARTUP:
        backup()

    try:
        schedule.every().day.at(settings.BACKUP_TIME).do(backup)
    except Exception:
        logger.error(
            f"Backup scheduler not started — BACKUP_TIME {settings.BACKUP_TIME!r} "
            "is not valid 24h HH:MM",
            exc_info=True,
            extra={"event": "backup_scheduler_disabled"}
        )
        return

    logger.info(
        f"Backup scheduler started — daily at {settings.BACKUP_TIME}, root {root}",
        extra={
            "event": "backup_scheduler_started",
            "backup_time": settings.BACKUP_TIME,
            "backup_root": str(root)
        }
    )

    while True:
        try:
            schedule.run_pending()
        except Exception:
            # The loop must outlive any single failure, otherwise one bad
            # run stops every future backup with no restart.
            logger.error(
                "Backup scheduler tick failed",
                exc_info=True,
                extra={"event": "backup_scheduler_tick_failed"}
            )

        time.sleep(max(settings.BACKUP_POLL_SECONDS, 1))

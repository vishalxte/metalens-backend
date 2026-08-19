from pydantic_settings import BaseSettings


class Settings(BaseSettings):

    PROJECT_NAME: str

    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    POSTGRES_HOST: str
    POSTGRES_PORT: int

    SECRET_KEY: str
    ALGORITHM: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int
    OPENAI_API_KEY: str

    SMTP_HOST: str
    SMTP_PORT: int
    SMTP_USER: str
    SMTP_PASSWORD: str
    FROM_EMAIL: str

    CACHE_TTL_SECONDS: int = 60 * 60 * 24  # 24h exact-match cache

    SEMANTIC_CACHE_SIMILARITY_THRESHOLD: float = 0.85  # cosine similarity, strict

        # --- Logging ---
    ENVIRONMENT: str = "development"  # "development" or "production"
    LOG_LEVEL: str = "INFO"           # DEBUG / INFO / WARNING / ERROR / CRITICAL
    LOG_FORMAT: str = "text"          # "text" (human-readable console) or "json" (structured)
    LOG_DIR: str = "logs"
    LOG_FILE_NAME: str = "app.log"
    LOG_MAX_BYTES: int = 10 * 1024 * 1024  # 10MB per file before rotating
    LOG_BACKUP_COUNT: int = 5              # keep 5 rotated files (app.log.1 ... app.log.5)
    SQL_ECHO: bool = False            # set True (or LOG_LEVEL=DEBUG) to log every SQL statement

  


    # --- Audit Framework (Module 1) ---
    # Every setting below has a default, so the existing .env keeps
    # working untouched. AUDIT_ENABLED is a hard kill switch: turning it
    # off makes every AuditService call a no-op without removing a single
    # call site, which is the safest possible rollback if audit writes
    # ever became a problem in production.
    AUDIT_ENABLED: bool = True
    AUDIT_RETENTION_DAYS: int = 365        # consumed by a future purge job
    AUDIT_MAX_PAGE_SIZE: int = 500         # cap on ?limit= for the read API
    AUDIT_EXPORT_MAX_ROWS: int = 100000    # cap on a single export request

    # --- Frontend Activity Tracking (Module 2) ---
    ACTIVITY_TRACKING_ENABLED: bool = True
    ACTIVITY_RETENTION_DAYS: int = 90
    ACTIVITY_MAX_BATCH_SIZE: int = 100     # events accepted per POST
    # Off by design — spec says do NOT track every mouse click. The
    # ActivityType.CLICK path already exists and is rejected until this
    # is flipped, so enabling it later is config, not code.
    ACTIVITY_CLICK_TRACKING_ENABLED: bool = False

    # --- Login session tracking (user_sessions) ---
    # Master switch. Off = no session rows written and no revocation
    # check performed; the JWT `jti` is still issued and still lands in
    # audit_logs.session_id, so correlation keeps working.
    SESSION_TRACKING_ENABLED: bool = True

    # Whether an authenticated request is checked against user_sessions
    # for revocation. This is the one setting that costs an indexed DB
    # read per request — turn it off to get pure stateless behaviour
    # back while keeping session records for reporting.
    SESSION_REVOCATION_CHECK_ENABLED: bool = True

    # last_seen_at is refreshed at most this often per session. Without
    # throttling, every read request in the app becomes a write.
    SESSION_TOUCH_INTERVAL_SECONDS: int = 60

    # Retention for CLOSED sessions (live ones are never purged).
    SESSION_RETENTION_DAYS: int = 180

    # --- Scheduled backup (app/backup/backupscheduler.py) ---
    # Every value has a default so the existing .env keeps working, and
    # nothing about backups is hardcoded in the module any more.
    BACKUP_ENABLED: bool = True

    # Where backup folders are written. MUST be an absolute path that is
    # NOT inside the project — the backup routine deletes previous
    # `backup_*` folders here before writing a new one, and BackupService
    # refuses to run if this is left empty.
    BACKUP_ROOT: str = ""

    # Project folders to include, comma-separated and RELATIVE to the
    # backend project root (the directory containing app/). Relative on
    # purpose: an absolute path would break the moment the project is
    # moved or deployed elsewhere, which is exactly what went wrong with
    # the original hardcoded /home/... paths.
    BACKUP_DIRS: str = "uploads"

    # Daily run time, 24h "HH:MM" local time.
    BACKUP_TIME: str = "16:15"

    # Run one backup immediately when the scheduler thread starts.
    BACKUP_ON_STARTUP: bool = True

    # How many previous backup_* folders to keep, NEWEST FIRST, before
    # the new one is written. 0 reproduces the original behaviour of
    # clearing them all; 2 keeps a little history in case the newest
    # backup is itself bad.
    BACKUP_KEEP: int = 2

    # pg_dump is frequently not on PATH on Windows. Point this at the
    # full executable, e.g.
    #   C:/Program Files/PostgreSQL/16/bin/pg_dump.exe
    PG_DUMP_PATH: str = "pg_dump"

    # How often the scheduler thread wakes to check for due jobs.
    BACKUP_POLL_SECONDS: int = 30

    class Config:
        env_file = ".env"


settings = Settings()

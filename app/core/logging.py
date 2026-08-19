"""
Centralized, structured logging setup for the whole backend.

Design goals (per the logging requirement):
  - One place configures every logger in the app — every module still
    just does `from app.core.logging import logger` (or
    `logging.getLogger("ai_document_search.<sub>")`) exactly as before.
  - Structured output: either human-readable text (local dev) or one-line
    JSON (production — easy to ship to any log aggregator), switched via
    Settings.LOG_FORMAT, no new third-party dependency required.
  - Every log line is automatically tagged with the current request_id
    (and, once auth has resolved, the calling user's email/customer_id)
    via the contextvars in app/core/request_context.py — this is what
    makes it possible to trace one request across API -> service ->
    repository -> DB just by grepping one request_id.
  - Console handler always on; a rotating file handler is added so logs
    survive process restarts and don't grow unbounded (Settings.LOG_DIR /
    LOG_FILE_NAME / LOG_MAX_BYTES / LOG_BACKUP_COUNT).
  - Log level configurable via Settings.LOG_LEVEL (env var), so the exact
    same code can run quiet in production and verbose in development.
"""
import json
import logging
import logging.handlers
import os
import traceback

from app.core.config import settings
from app.core.request_context import (
    get_request_id,
    get_current_user_email,
    get_current_customer_id,
    get_current_super_admin_id
)


class RequestContextFilter(logging.Filter):
    """
    Injects the current request's request_id / user_email / customer_id /
    super_admin_id (from contextvars) onto every LogRecord that passes
    through, so both formatters below can reference them unconditionally
    — outside of a request (e.g. a startup log, or a script), these fall
    back to "-" rather than raising a KeyError in the formatter.

    super_admin_id is the Super Admin who owns the tenant making this
    request (Customer.created_by), or the caller's own id when the
    caller IS the Super Admin — set in get_current_user() once the JWT
    is resolved. Lets every log line be traced back to which Super
    Admin's tenant it belongs to, not just which customer_id.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        record.user_email = get_current_user_email() or "-"
        customer_id = get_current_customer_id()
        record.customer_id = customer_id if customer_id is not None else "-"
        super_admin_id = get_current_super_admin_id()
        record.super_admin_id = super_admin_id if super_admin_id is not None else "-"
        return True


class JsonFormatter(logging.Formatter):
    """
    One JSON object per line — easy to parse/ingest in production
    (CloudWatch, ELK, Loki, whatever). Includes exception + stack trace
    when present, and any extra fields passed via `logger.info(..., extra={...})`.
    """

    # Attributes every stdlib LogRecord already carries — anything else on
    # the record is a caller-supplied `extra={...}` field and gets folded
    # into the JSON output too.
    _STANDARD_ATTRS = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "taskName"
    }

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "user_email": getattr(record, "user_email", "-"),
            "customer_id": getattr(record, "customer_id", "-"),
            "super_admin_id": getattr(record, "super_admin_id", "-"),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno
        }

        for key, value in record.__dict__.items():
            if key not in self._STANDARD_ATTRS and key not in payload and not key.startswith("_"):
                try:
                    json.dumps(value)  # only include JSON-serializable extras
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = str(value)

        if record.exc_info:
            payload["exception"] = "".join(
                traceback.format_exception(*record.exc_info)
            )

        return json.dumps(payload, default=str)


TEXT_FORMAT = (
    "%(asctime)s | %(levelname)-8s | req=%(request_id)s | user=%(user_email)s "
    "| customer=%(customer_id)s | super_admin=%(super_admin_id)s "
    "| %(name)s:%(funcName)s:%(lineno)d | %(message)s"
)


def _build_formatter() -> logging.Formatter:
    if settings.LOG_FORMAT.lower() == "json":
        return JsonFormatter()
    return logging.Formatter(TEXT_FORMAT)


def _resolve_level(level_name: str) -> int:
    return getattr(logging, level_name.upper(), logging.INFO)


def setup_logging() -> None:
    """
    Idempotent: safe to call more than once (e.g. once at import time,
    again explicitly in main.py's startup event) — clears any handlers a
    previous call already attached instead of duplicating them.
    """
    root_level = _resolve_level(settings.LOG_LEVEL)
    formatter = _build_formatter()
    context_filter = RequestContextFilter()

    app_logger = logging.getLogger("ai_document_search")
    app_logger.setLevel(root_level)
    app_logger.handlers.clear()
    app_logger.propagate = False

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(context_filter)
    app_logger.addHandler(console_handler)

    # Rotating file handler — logs survive restarts, and old files roll
    # off automatically instead of growing forever.
    try:
        os.makedirs(settings.LOG_DIR, exist_ok=True)
        file_path = os.path.join(settings.LOG_DIR, settings.LOG_FILE_NAME)
        file_handler = logging.handlers.RotatingFileHandler(
            file_path,
            maxBytes=settings.LOG_MAX_BYTES,
            backupCount=settings.LOG_BACKUP_COUNT,
            encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(context_filter)
        app_logger.addHandler(file_handler)
    except OSError:
        # If the filesystem is read-only or the directory can't be
        # created (e.g. some container setups), fall back to
        # console-only logging rather than crashing the app over logging.
        app_logger.warning(
            "Could not set up file logging at %s — continuing with console logging only",
            settings.LOG_DIR
        )

    # Silence noisy third-party libraries at INFO (they're chatty at
    # DEBUG especially) unless the app itself is explicitly set to DEBUG,
    # in which case let them through too — useful when actually
    # diagnosing an HTTP/OpenAI-level problem.
    third_party_level = logging.DEBUG if root_level <= logging.DEBUG else logging.ERROR
    for noisy_logger in ("openai", "httpx", "urllib3"):
        logging.getLogger(noisy_logger).setLevel(third_party_level)

    # SQLAlchemy's own query logger — only chatty when explicitly asked
    # for (Settings.SQL_ECHO, or LOG_LEVEL=DEBUG), since every single SQL
    # statement + bound params is a lot of volume otherwise.
    sql_level = logging.INFO if (settings.SQL_ECHO or root_level <= logging.DEBUG) else logging.WARNING
    logging.getLogger("sqlalchemy.engine").setLevel(sql_level)


setup_logging()

logger = logging.getLogger("ai_document_search")

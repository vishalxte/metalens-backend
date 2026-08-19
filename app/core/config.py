from base64 import b64decode
from typing import Any

from pydantic import field_validator
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
    CACHE_TTL_SECONDS: int = 60 * 60 * 24
    SEMANTIC_CACHE_SIMILARITY_THRESHOLD: float = 0.85
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "text"
    LOG_DIR: str = "logs"
    LOG_FILE_NAME: str = "app.log"
    LOG_MAX_BYTES: int = 10 * 1024 * 1024
    LOG_BACKUP_COUNT: int = 5
    SQL_ECHO: bool = False
    AUDIT_ENABLED: bool = True
    AUDIT_RETENTION_DAYS: int = 365
    AUDIT_MAX_PAGE_SIZE: int = 500
    AUDIT_EXPORT_MAX_ROWS: int = 100000
    ACTIVITY_TRACKING_ENABLED: bool = True
    ACTIVITY_RETENTION_DAYS: int = 90
    ACTIVITY_MAX_BATCH_SIZE: int = 100
    ACTIVITY_CLICK_TRACKING_ENABLED: bool = False
    SESSION_TRACKING_ENABLED: bool = True
    SESSION_REVOCATION_CHECK_ENABLED: bool = True
    SESSION_TOUCH_INTERVAL_SECONDS: int = 60
    SESSION_RETENTION_DAYS: int = 180
    BACKUP_ENABLED: bool = True
    BACKUP_ROOT: str = ""
    BACKUP_DIRS: str = "uploads"
    BACKUP_TIME: str = "16:15"
    BACKUP_ON_STARTUP: bool = True
    BACKUP_KEEP: int = 2
    PG_DUMP_PATH: str = "pg_dump"
    BACKUP_POLL_SECONDS: int = 30

    APP_ENCRYPTION_KEY: str

    @field_validator("APP_ENCRYPTION_KEY")
    @classmethod
    def validate_encryption_key(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("APP_ENCRYPTION_KEY is required")
        try:
            decoded = b64decode(value, validate=True)
        except Exception as exc:
            raise ValueError("APP_ENCRYPTION_KEY must be valid Base64") from exc
        if len(decoded) != 32:
            raise ValueError("APP_ENCRYPTION_KEY must decode to exactly 32 bytes")
        return value

    class Config:
        env_file = ".env"


settings = Settings()

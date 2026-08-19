from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base

from app.core.config import settings

DATABASE_URL = (
    f"postgresql://"
    f"{settings.POSTGRES_USER}:"
    f"{settings.POSTGRES_PASSWORD}@"
    f"{settings.POSTGRES_HOST}:"
    f"{settings.POSTGRES_PORT}/"
    f"{settings.POSTGRES_DB}"
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    # Emits every SQL statement + bound params through  standard
    # "sqlalchemy.engine" logger (level wired up in app/core/logging.py)
    # when explicitly enabled or when running at DEBUG — satisfies the
    # "database queries and transactions" logging requirement without
    # hand-instrumenting every repository method individually.
    echo=settings.SQL_ECHO or settings.LOG_LEVEL.upper() == "DEBUG"
)

Base = declarative_base()

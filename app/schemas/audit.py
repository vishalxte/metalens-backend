"""
Read-side schemas for the Audit API.

There is deliberately NO write schema. Audit rows are never created from
an HTTP request — they are emitted by business services through
AuditService. An audit trail that can be written over HTTP is forgeable.
"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class AuditLogResponse(BaseModel):
    """One audit row, flat, exactly as stored — so a CSV export and the
    JSON API share a single serialization and can never drift."""

    id: int
    event_time: datetime

    user_id: Optional[int] = None
    user_email: Optional[str] = None
    session_id: Optional[str] = None
    ip_address: Optional[str] = None

    category: str
    action: str
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    status: str

    details: Optional[dict[str, Any]] = None

    class Config:
        from_attributes = True


class AuditLogPage(BaseModel):
    """
    Pagination envelope. `total` is a separate COUNT so the UI can render
    real page numbers rather than an endless "load more".
    """

    total: int
    limit: int
    offset: int
    items: list[AuditLogResponse]


class AuditCategoryCount(BaseModel):
    category: str
    total: int


class AuditSummary(BaseModel):
    total: int
    by_category: list[AuditCategoryCount]


class AuditMetadata(BaseModel):
    """
    The vocabulary the API accepts — lets the frontend build filter
    dropdowns from the server instead of hardcoding constants that would
    silently drift out of sync with app/models/audit_log.py.
    """

    categories: list[str]
    statuses: list[str]
    actions: list[str]
    target_types: list[str]

"""
Schemas for the Frontend Activity Tracking API.

Unlike audit.py this DOES have write schemas — activity events
legitimately originate in the browser. What the client may send is
tightly bounded: behavioural fields only.

There is intentionally no user_id, session_id, ip, user agent or
timestamp on the way in. ActivityService stamps identity and time from
the request context, so a client cannot attribute activity to another
user, another session, or another moment.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ActivityEventIn(BaseModel):
    """
    One tracked event. Everything except `action` is optional, so a bare
    PAGE_VIEW is as cheap to send as a fully described NAVIGATION.
    """

    action: str = Field(
        ...,
        max_length=32,
        description="PAGE_VIEW | NAVIGATION | CLICK | QUESTION_SUBMITTED"
    )

    page: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Logical page name, e.g. DASHBOARD, CHAT, DOCUMENTS"
    )

    from_page: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Previous page. NAVIGATION only."
    )

    feature: Optional[str] = Field(
        default=None,
        max_length=64,
        description="What was used, e.g. UPLOAD_DOCUMENT. CLICK only."
    )

    question_text: Optional[str] = Field(
        default=None,
        description=(
            "The question as typed. QUESTION_SUBMITTED only — ignored "
            "and stored as NULL for every other action."
        )
    )

    target_type: Optional[str] = Field(
        default=None,
        max_length=32,
        description="DOCUMENT | USER | CUSTOMER | CONVERSATION"
    )

    target_id: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Which row was acted on, e.g. the document id."
    )


class ActivityBatchIn(BaseModel):
    """
    The browser queues events and flushes them together (on navigation,
    on tab hide, on a timer), so batching is the normal path.
    """

    events: list[ActivityEventIn] = Field(default_factory=list)


class ActivityIngestResult(BaseModel):
    accepted: int
    rejected: int


class ActivityLogResponse(BaseModel):

    id: int
    event_time: datetime

    user_id: Optional[int] = None
    user_email: Optional[str] = None
    session_id: Optional[str] = None

    action: str
    page: Optional[str] = None
    from_page: Optional[str] = None
    feature: Optional[str] = None
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    question_text: Optional[str] = None

    class Config:
        from_attributes = True


class ActivityLogPage(BaseModel):

    total: int
    limit: int
    offset: int
    items: list[ActivityLogResponse]


class ActivityMetadata(BaseModel):
    """What the ingest endpoints currently accept."""

    enabled: bool
    max_batch_size: int
    allowed_actions: list[str]
    pages: list[str]
    target_types: list[str]

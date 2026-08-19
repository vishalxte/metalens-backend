"""
Frontend Activity Tracking API.

Two access tiers in one router, which is why the Super Admin gate is NOT
applied at router level here (unlike audit.py):

  - INGEST  (/events, /event)
      Any authenticated user. Every logged-in user generates their own
      activity, so every logged-in user must be able to post it.

  - READ    (/logs)
      Super Admin only, via the existing get_super_admin dependency —
      activity is user-behaviour data and shouldn't be browsable by peers.

Ingest routes always return 200 with a count. They never return an error
for a rejected event: telemetry failing must not surface as a broken UI
interaction to a user who is simply navigating the app.

Removed in the logs redesign: /session/start, /session/end and /summary.
Those depended on SESSION_START / SESSION_END / FEATURE_USAGE events and
the duration_ms column, none of which exist any more.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_user, get_super_admin
from app.core.config import settings
from app.database.session import get_db
from app.models.activity_log import (
    ActivityAction,
    ActivityPage,
    ActivityTargetType
)
from app.repositories.activity_repository import ActivityRepository
from app.schemas.activity import (
    ActivityEventIn,
    ActivityBatchIn,
    ActivityIngestResult,
    ActivityLogPage,
    ActivityMetadata
)
from app.services.activity_service import activity_service

router = APIRouter(
    prefix="/activity",
    tags=["Activity Tracking"]
)


# ─────────────────────────────────────────
# INGEST — batch. The primary path: the browser queues events and
# flushes them together on navigation / tab hide / timer.
# ─────────────────────────────────────────
@router.post("/events", response_model=ActivityIngestResult, status_code=200)
def ingest_activity_events(
    payload: ActivityBatchIn,
    current_user=Depends(get_current_user)
):
    result = activity_service.track_batch(
        [event.model_dump() for event in payload.events]
    )

    return ActivityIngestResult(**result)


# ─────────────────────────────────────────
# INGEST — single event, for a one-off ping where queueing is pointless.
# ─────────────────────────────────────────
@router.post("/event", response_model=ActivityIngestResult, status_code=200)
def ingest_activity_event(
    payload: ActivityEventIn,
    current_user=Depends(get_current_user)
):
    accepted = activity_service.track(**payload.model_dump())

    return ActivityIngestResult(
        accepted=1 if accepted else 0,
        rejected=0 if accepted else 1
    )


# ─────────────────────────────────────────
# READ — Super Admin only.
#
# `search` runs over question_text, which is what answers the question
# this redesign exists for: "which question did the user ask?"
# ─────────────────────────────────────────
@router.get(
    "/logs",
    response_model=ActivityLogPage,
    status_code=200,
    dependencies=[Depends(get_super_admin)]
)
def list_activity_logs(
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1),
    offset: int = Query(default=0, ge=0),
    action: Optional[str] = None,
    page: Optional[str] = None,
    feature: Optional[str] = None,
    user_id: Optional[int] = None,
    user_email: Optional[str] = None,
    session_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    search: Optional[str] = Query(
        default=None,
        description="Free-text search over question_text."
    )
):
    limit = min(limit, settings.AUDIT_MAX_PAGE_SIZE)

    filters = {
        "action": action,
        "page": page,
        "feature": feature,
        "user_id": user_id,
        "user_email": user_email,
        "session_id": session_id,
        "target_type": target_type,
        "target_id": target_id,
        "start_date": start_date,
        "end_date": end_date,
        "search": search
    }

    repo = ActivityRepository(db)

    return ActivityLogPage(
        total=repo.count(**filters),
        limit=limit,
        offset=offset,
        items=repo.search(limit=limit, offset=offset, **filters)
    )


# ─────────────────────────────────────────
# The vocabulary the ingest endpoints accept, so the frontend can
# discover it at runtime rather than hardcoding constants that would
# drift out of sync with app/models/activity_log.py.
# ─────────────────────────────────────────
@router.get("/metadata", response_model=ActivityMetadata, status_code=200)
def get_activity_metadata(
    current_user=Depends(get_current_user)
):
    pages = sorted(
        value
        for name, value in vars(ActivityPage).items()
        if not name.startswith("_") and isinstance(value, str)
    )

    return ActivityMetadata(
        enabled=settings.ACTIVITY_TRACKING_ENABLED,
        max_batch_size=settings.ACTIVITY_MAX_BATCH_SIZE,
        allowed_actions=list(ActivityAction.ALL),
        pages=pages,
        target_types=list(ActivityTargetType.ALL)
    )

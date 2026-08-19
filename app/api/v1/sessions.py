"""
Session administration API — Super Admin only.

Mounted under /audit/sessions rather than a top-level /sessions prefix,
because this is audit-console functionality and shares the audit API's
access rule exactly. Kept in its own module so audit.py stays focused on
the log itself.

What this enables that a pure stateless JWT cannot:

    GET  /audit/sessions/active            -> who is logged in right now
    GET  /audit/sessions                   -> full session history
    GET  /audit/sessions/{session_id}      -> one session + its audit trail
    POST /audit/sessions/{session_id}/revoke -> forced logout
    POST /audit/sessions/users/{user_id}/revoke -> log out everywhere

Every route is new; nothing here changes an existing endpoint.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_super_admin
from app.core.config import settings
from app.database.session import get_db
from app.models.audit_log import AuditCategory, AuditAction, AuditStatus, TargetType
from app.models.user_session import SessionEndReason
from app.repositories.audit_repository import AuditRepository
from app.repositories.user_session_repository import UserSessionRepository
from app.schemas.audit import AuditLogResponse
from app.schemas.session import (
    UserSessionResponse,
    UserSessionPage,
    SessionRevokeResult
)
from app.services.audit_service import audit_service
from app.services.session_service import session_service

router = APIRouter(
    prefix="/audit/sessions",
    tags=["Sessions"],
    # Router-level gate, same as the audit router — a route added here
    # later cannot accidentally ship unprotected.
    dependencies=[Depends(get_super_admin)]
)


def _sweep_expired() -> None:
    """
    Stamps EXPIRED on any session whose token has lapsed, before the
    caller reads the list.

    A write on a read path, which is normally worth avoiding — justified
    here because these are Super-Admin-only screens with negligible
    traffic, it is a single indexed UPDATE that usually affects zero
    rows, and it is the difference between the session list reflecting
    reality and showing a wall of never-closed sessions. The alternative
    (a scheduler) is more moving parts for a table only an admin reads.

    Never raises — session_service.expire_stale() swallows everything,
    so a failure here cannot break the listing.
    """
    session_service.expire_stale()


def _to_response(row) -> UserSessionResponse:
    """
    is_active is derived at read time (see the schema comment) — a
    session that simply timed out has no row change to signal it.

    WHY THIS DOES NOT USE model_validate(row)
    ─────────────────────────────────────────
    UserSessionResponse.is_active is a bool, but UserSession.is_active is
    a METHOD. With `from_attributes = True`, model_validate() does a
    plain getattr for every field name, so it read the bound method and
    handed that to the bool validator:

        ValidationError: is_active
          Input should be a valid boolean
          [input_value=<bound method UserSession.is_active ...>]

    The `payload.is_active = row.is_active()` line on the next statement
    was meant to supply the real value, but validation already failed by
    then, so GET /audit/sessions/active, GET /audit/sessions and GET
    /audit/sessions/{id} all returned 500.

    Copying the stored columns explicitly and calling the method for the
    computed one removes the name collision instead of working around
    it, and keeps the schema and the model free to disagree about what
    `is_active` means.
    """
    data = {
        name: getattr(row, name)
        for name in UserSessionResponse.model_fields
        if name != "is_active"
    }
    data["is_active"] = row.is_active()

    return UserSessionResponse(**data)


# ─────────────────────────────────────────
# Currently-live sessions. Registered before the /{session_id} route so
# the literal path is never captured by the path parameter.
# ─────────────────────────────────────────
@router.get("/active", response_model=UserSessionPage, status_code=200)
def list_active_sessions(
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1),
    offset: int = Query(default=0, ge=0),
    user_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    role: Optional[str] = None
):
    _sweep_expired()

    limit = min(limit, settings.AUDIT_MAX_PAGE_SIZE)

    filters = {
        "user_id": user_id,
        "customer_id": customer_id,
        "role": role,
        "active_only": True
    }

    repo = UserSessionRepository(db)

    return UserSessionPage(
        total=repo.count(**filters),
        limit=limit,
        offset=offset,
        items=[
            _to_response(row)
            for row in repo.search(limit=limit, offset=offset, **filters)
        ]
    )


# ─────────────────────────────────────────
# Full session history, filterable.
# ─────────────────────────────────────────
@router.get("", response_model=UserSessionPage, status_code=200)
def list_sessions(
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1),
    offset: int = Query(default=0, ge=0),
    user_id: Optional[int] = None,
    user_email: Optional[str] = None,
    role: Optional[str] = None,
    customer_id: Optional[int] = None,
    active_only: bool = False,
    ended_reason: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
):
    _sweep_expired()

    limit = min(limit, settings.AUDIT_MAX_PAGE_SIZE)

    filters = {
        "user_id": user_id,
        "user_email": user_email,
        "role": role,
        "customer_id": customer_id,
        "active_only": active_only,
        "ended_reason": ended_reason,
        "start_date": start_date,
        "end_date": end_date
    }

    repo = UserSessionRepository(db)

    return UserSessionPage(
        total=repo.count(**filters),
        limit=limit,
        offset=offset,
        items=[
            _to_response(row)
            for row in repo.search(limit=limit, offset=offset, **filters)
        ]
    )


# ─────────────────────────────────────────
# Housekeeping: stamp EXPIRED on sessions whose token lapsed without an
# explicit logout. Cosmetic only — those sessions are already treated as
# dead everywhere — but it's what makes "ended by logout vs. timed out"
# reportable instead of leaving a NULL reason forever.
# ─────────────────────────────────────────
@router.post("/expire-stale", response_model=SessionRevokeResult, status_code=200)
def expire_stale_sessions(
    db: Session = Depends(get_db)
):
    count = UserSessionRepository(db).expire_stale()

    return SessionRevokeResult(
        revoked=count,
        message=f"{count} lapsed session(s) marked as EXPIRED"
    )


# ─────────────────────────────────────────
# Forced logout: every live session for one user.
# ─────────────────────────────────────────
@router.post(
    "/users/{user_id}/revoke",
    response_model=SessionRevokeResult,
    status_code=200
)
def revoke_user_sessions(
    user_id: int,
    current_user=Depends(get_super_admin)
):
    count = session_service.end_all_for_user(
        user_id,
        reason=SessionEndReason.REVOKED,
        revoked_by_user_id=current_user.id
    )

    audit_service.log_system_event(
        action=AuditAction.SESSION_REVOKED,
        category=AuditCategory.SECURITY,
        target_type=TargetType.USER,
        target_id=str(user_id),
        status=AuditStatus.SUCCESS,
        details={
            "operation": "revoke_all_sessions",
            "target_user_id": user_id,
            "sessions_revoked": count
        }
    )

    return SessionRevokeResult(
        revoked=count,
        message=f"{count} session(s) revoked for user {user_id}"
    )


# ─────────────────────────────────────────
# One session's detail, plus everything that happened during it. This is
# the screen an investigator actually wants: a single login, and every
# audited action taken under it.
# ─────────────────────────────────────────
@router.get("/{session_id}", status_code=200)
def get_session(
    session_id: str,
    db: Session = Depends(get_db),
    include_audit: bool = Query(
        default=True,
        description="Include the audit events recorded during this session."
    ),
    audit_limit: int = Query(default=100, ge=1, le=1000)
):
    row = UserSessionRepository(db).get_by_session_id(session_id)

    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    result = {"session": _to_response(row)}

    if include_audit:
        events = AuditRepository(db).search(
            limit=audit_limit,
            offset=0,
            session_id=session_id
        )
        result["audit_events"] = [
            AuditLogResponse.model_validate(e) for e in events
        ]
        result["audit_event_count"] = AuditRepository(db).count(
            session_id=session_id
        )

    return result


# ─────────────────────────────────────────
# Forced logout: one specific session.
# ─────────────────────────────────────────
@router.post(
    "/{session_id}/revoke",
    response_model=SessionRevokeResult,
    status_code=200
)
def revoke_session(
    session_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_super_admin)
):
    row = UserSessionRepository(db).get_by_session_id(session_id)

    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    if row.ended_at is not None:
        # Not an error — the desired end state already holds.
        return SessionRevokeResult(
            revoked=0,
            message=f"Session was already ended ({row.ended_reason})"
        )

    ended = session_service.end(
        session_id,
        reason=SessionEndReason.REVOKED,
        revoked_by_user_id=current_user.id
    )

    audit_service.log_system_event(
        action=AuditAction.SESSION_REVOKED,
        category=AuditCategory.SECURITY,
        target_type=TargetType.SESSION,
        target_id=session_id,
        status=AuditStatus.SUCCESS if ended else AuditStatus.FAILURE,
        details={
            "operation": "revoke_session",
            "target_user_id": row.user_id,
            "target_email": row.user_email
        }
    )

    return SessionRevokeResult(
        revoked=1 if ended else 0,
        message=(
            "Session revoked. The user will be signed out on their next request."
            if ended
            else "Session could not be revoked."
        )
    )

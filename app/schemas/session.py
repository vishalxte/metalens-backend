"""
Read-side schemas for the session admin API.

No write schema for creating a session: sessions are opened only by a
successful login, never by an HTTP call. The one mutation exposed is
revocation, which takes no body — the session id is in the path.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class UserSessionResponse(BaseModel):

    id: int

    # The JWT `jti`. Join key into audit_logs.session_id and
    # activity_logs.session_id.
    session_id: str

    user_id: Optional[int] = None
    user_email: Optional[str] = None
    role: Optional[str] = None
    customer_id: Optional[int] = None

    login_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    ended_at: Optional[datetime] = None
    ended_reason: Optional[str] = None
    revoked_by_user_id: Optional[int] = None

    ip_address: Optional[str] = None
    user_agent: Optional[str] = None

    # Derived from user_agent at login — what the session list renders
    # instead of the raw UA string.
    browser: Optional[str] = None
    platform: Optional[str] = None
    device_name: Optional[str] = None

    created_at: datetime
    updated_at: Optional[datetime] = None

    # Computed rather than stored: a session can lapse purely by the
    # clock passing expires_at, with no row update. Deriving it on read
    # means the answer is never stale.
    is_active: bool = False

    class Config:
        from_attributes = True


class UserSessionPage(BaseModel):

    total: int
    limit: int
    offset: int
    items: list[UserSessionResponse]


class SessionRevokeResult(BaseModel):
    """Returned by both revoke endpoints so the caller can confirm the count."""

    revoked: int
    message: str

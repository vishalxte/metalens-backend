"""
Login session lifecycle.

Relationship to the other two tables — all three are separate on purpose:

  audit_logs      WHAT happened (security/compliance trail, append-only)
  activity_logs   HOW the UI was used (product analytics, high volume)
  user_sessions   WHO is/was logged in, and WHEN that login ended

`user_sessions.session_id` is the JWT's `jti` claim, which is also
written into `audit_logs.session_id` and `activity_logs.session_id`.
That single shared key is what lets you take one row from any of the
three tables and pull the complete story of that login:

    SELECT * FROM audit_logs    WHERE session_id = :jti;
    SELECT * FROM activity_logs WHERE session_id = :jti;
    SELECT * FROM user_sessions WHERE session_id = :jti;

WHY THIS TABLE EXISTS WHEN JWT IS STATELESS
───────────────────────────────────────────
A pure `jti`-in-the-token design gives correlation but cannot answer the
two questions every enterprise deployment eventually gets asked:

  1. "Who is logged in right now?"     -> needs server-side state
  2. "Log that person out immediately" -> needs revocation

A stateless token is valid until it expires; there is no way to retract
it without a server-side record to check against. This table is that
record. The token itself stays stateless and unchanged — this is a
lookup *alongside* verification, not a replacement for it.

FAILURE POSTURE (important)
───────────────────────────
Session bookkeeping must never lock users out because of an
infrastructure problem. SessionService therefore FAILS OPEN: if this
table is unreachable, or the row is missing (e.g. a token issued before
this feature existed), the request proceeds as it did before. Only an
explicit, successfully-read `ended_at` ever rejects a request.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Integer,
    BigInteger,
    String,
    DateTime,
    ForeignKey,
    Index
)

from app.database.database import Base


def _as_aware(value: datetime) -> datetime:
    """
    Coerces a datetime to UTC-aware.

    On PostgreSQL these columns are TIMESTAMPTZ and always come back
    aware, so this is normally a no-op. It matters when a row arrives
    from somewhere that dropped the offset — a restored dump, a bulk
    import, a driver or test harness without timezone support. Without
    it, comparing a naive stored value against an aware `now()` raises
    TypeError and would take down the revocation check, which fails open
    and would therefore let a REVOKED session through.
    """
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value


class SessionEndReason:
    """
    Why a session stopped being usable. Plain string constants, matching
    the style of app/models/user.py::Role and the audit constants.
    """

    # The user clicked Log Out — POST /auth/logout.
    LOGOUT = "LOGOUT"

    # The token's own `exp` passed. Sessions are not force-closed at
    # expiry in real time; expire_stale() backfills the reason so
    # reporting doesn't show a session as "still open" forever.
    EXPIRED = "EXPIRED"

    # An administrator forcibly terminated it — POST
    # /audit/sessions/{session_id}/revoke. This is the reason the table
    # exists at all.
    REVOKED = "REVOKED"

    # The account was deactivated or deleted while the session was live.
    ACCOUNT_DISABLED = "ACCOUNT_DISABLED"

    ALL = (LOGOUT, EXPIRED, REVOKED, ACCOUNT_DISABLED)


class UserSession(Base):

    __tablename__ = "user_sessions"

    id = Column(
        BigInteger,
        primary_key=True
    )

    # The JWT `jti`. UNIQUE because a jti identifies exactly one login,
    # and the uniqueness constraint is also what makes the
    # once-per-request lookup a single index hit.
    session_id = Column(
        String(64),
        unique=True,
        nullable=False,
        index=True
    )

    # SET NULL rather than CASCADE: deleting a user must not erase the
    # record that they were logged in. user_email preserves the identity.
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True
    )

    user_email = Column(
        String(320),
        nullable=True
    )

    role = Column(
        String(32),
        nullable=True
    )

    customer_id = Column(
        Integer,
        ForeignKey("customers.id", ondelete="SET NULL"),
        nullable=True
    )

    login_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    # Updated at most once per SESSION_TOUCH_INTERVAL_SECONDS, not on
    # every request — otherwise a chatty client would turn every read
    # into a write. Precision to the minute is plenty for "when was this
    # session last active".
    last_seen_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    # Mirrors the token's `exp`. Stored so "active sessions" can be
    # answered from this table alone, without decoding any token.
    expires_at = Column(
        DateTime(timezone=True),
        nullable=False
    )

    # NULL while the session is live. Any non-NULL value means the token
    # must be rejected even though it is still cryptographically valid
    # and unexpired — this column IS the revocation mechanism.
    ended_at = Column(
        DateTime(timezone=True),
        nullable=True
    )

    ended_reason = Column(
        String(32),
        nullable=True
    )

    # Which administrator forced the termination, for REVOKED sessions.
    revoked_by_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True
    )

    # Captured at login. A later request arriving on the same session
    # from a very different IP is the classic session-hijacking signal —
    # storing the origin makes that detectable after the fact.
    ip_address = Column(
        String(64),
        nullable=True
    )

    user_agent = Column(
        String(512),
        nullable=True
    )

    # ── Derived from user_agent at login, once ────────────────────────
    # Denormalized for the session list, which must render "Chrome on
    # Windows" rather than a 200-char UA string. Parsing on read would
    # repeat the work for every row of every page; the raw user_agent is
    # still stored above, so these remain recomputable and are not a
    # second source of truth. See app/core/user_agent.py.
    browser = Column(
        String(64),
        nullable=True
    )

    platform = Column(
        String(64),
        nullable=True
    )

    # Human-readable summary ("Chrome on Windows 10/11"), NOT a hardware
    # identifier — a UA string cannot identify a specific device, and
    # implying otherwise in an audit context would be misleading.
    device_name = Column(
        String(128),
        nullable=True
    )

    # NOTE: `country` / `city` / `refresh_jti` were removed (migration
    # a6b7c8d9e0f1). They were speculative — nothing populated them, and
    # an always-NULL column is worse than no column: it implies data
    # exists. Geolocation needs a GeoIP source that is a deployment
    # decision, and refresh_jti needs refresh tokens to exist first.
    # Both are one ADD COLUMN away when actually required.

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    # Bumped by SQLAlchemy on every UPDATE — which for this table means
    # a throttled last_seen_at touch, or the session being closed.
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        # "Which sessions are live?" — the partial index matches the
        # exact predicate is_active() uses, so the active-sessions screen
        # never scans closed sessions.
        Index(
            "ix_user_sessions_active",
            expires_at,
            postgresql_where=(ended_at.is_(None))
        ),
        Index("ix_user_sessions_user_id_login_at", user_id, login_at.desc()),
        Index("ix_user_sessions_customer_id", customer_id),
        Index("ix_user_sessions_login_at", login_at.desc()),
    )

    def is_active(self, now: datetime = None) -> bool:
        """Live == never closed AND not past its token's expiry."""
        if self.ended_at is not None:
            return False

        if self.expires_at is None:
            return False

        now = now or datetime.now(timezone.utc)

        return _as_aware(self.expires_at) > _as_aware(now)

    def __repr__(self) -> str:
        return (
            f"<UserSession {self.session_id} user={self.user_email} "
            f"{'active' if self.is_active() else self.ended_reason or 'expired'}>"
        )

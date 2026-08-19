"""
Frontend Activity Service — the only writer of activity_logs.

Completely independent of AuditService: separate table, separate
retention, separate access rules, no shared code path. The only thing
the two have in common is the never-break-the-caller guarantee and the
isolated-session pattern.

TRUST BOUNDARY — the important difference from AuditService
───────────────────────────────────────────────────────────
Activity events originate in the BROWSER, so everything the client sends
is treated as untrusted input and validated here:

  - `action` must be one of the four known constants; anything else is
    dropped rather than stored, so the table cannot accumulate junk;
  - page / feature / from_page are length-capped;
  - identity (user_id, session_id) is NEVER read from the payload — it
    is stamped server-side from the request context, so a client cannot
    attribute activity to another user or another session.

`question_text` is the one field stored verbatim, because the whole
point of QUESTION_SUBMITTED is to record what was actually asked. It is
NOT sanitized — see the note on the model column.
"""
from datetime import datetime, timezone
from typing import Optional, Sequence

from app.core.config import settings
from app.core.logging import logger
from app.core.request_context import get_audit_context
from app.database.session import SessionLocal
from app.models.activity_log import (
    ActivityLog,
    ActivityAction,
    ActivityTargetType
)
from app.repositories.activity_repository import ActivityRepository


_MAX_LEN = {
    "user_email": 320,
    "session_id": 64,
    "action": 32,
    "page": 64,
    "from_page": 64,
    "feature": 64,
    "target_type": 32,
    "target_id": 128
}

# A question longer than this is not a question. Capped rather than
# rejected so the event is still recorded, and generous enough that no
# realistic question is affected.
_MAX_QUESTION_CHARS = 8000


def _truncate(value: Optional[str], field: str) -> Optional[str]:
    if value is None:
        return None

    limit = _MAX_LEN.get(field)
    text = str(value)

    return text[:limit] if limit else text


class ActivityService:

    def _build_entry(
        self,
        context: dict,
        action: str,
        page: Optional[str] = None,
        from_page: Optional[str] = None,
        feature: Optional[str] = None,
        question_text: Optional[str] = None,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None
    ) -> Optional[ActivityLog]:
        """
        Returns None for an event that fails validation, so a single bad
        item in a batch is dropped without discarding the whole batch.
        """
        if action not in ActivityAction.ALL:
            logger.debug(
                f"Activity event rejected — unsupported action={action!r}",
                extra={"event": "activity_event_rejected", "activity_action": action}
            )
            return None

        # question_text belongs to exactly one action. Clearing it
        # elsewhere keeps the column meaningful and stops a client
        # smuggling arbitrary text in on a PAGE_VIEW.
        if action != ActivityAction.QUESTION_SUBMITTED:
            question_text = None
        elif question_text:
            question_text = str(question_text)[:_MAX_QUESTION_CHARS]

        # An unrecognised target_type is dropped rather than stored, for
        # the same reason unknown actions are: an unconstrained string
        # column fills with typos and stops being filterable.
        if target_type and target_type not in ActivityTargetType.ALL:
            target_type = None

        return ActivityLog(
            event_time=datetime.now(timezone.utc),
            # Identity always from context, never from the payload — a
            # client cannot claim to be another user.
            user_id=context["user_id"],
            user_email=_truncate(context["user_email"], "user_email"),
            # The JWT `jti` — same key as user_sessions and audit_logs.
            session_id=_truncate(context["session_id"], "session_id"),
            action=_truncate(action, "action"),
            page=_truncate(page, "page"),
            from_page=_truncate(from_page, "from_page"),
            feature=_truncate(feature, "feature"),
            # Target IS taken from the payload: only the browser knows
            # which row the user clicked. It is a plain identifier, not
            # a permission — nothing is authorised on the strength of it.
            target_type=_truncate(target_type, "target_type"),
            target_id=_truncate(
                str(target_id) if target_id is not None else None,
                "target_id"
            ),
            question_text=question_text
        )

    def track(
        self,
        action: str,
        page: Optional[str] = None,
        from_page: Optional[str] = None,
        feature: Optional[str] = None,
        question_text: Optional[str] = None,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None
    ) -> bool:
        """
        Records a single event. Returns whether it was persisted — safe
        to return here because the caller is the activity endpoint
        itself, which reports a count back to the browser. Never raises.
        """
        try:
            if not settings.ACTIVITY_TRACKING_ENABLED:
                return False

            entry = self._build_entry(
                get_audit_context(),
                action=action,
                page=page,
                from_page=from_page,
                feature=feature,
                question_text=question_text,
                target_type=target_type,
                target_id=target_id
            )

            if entry is None:
                return False

            db = SessionLocal()
            try:
                ActivityRepository(db).create(entry)
                return True
            finally:
                db.close()

        except Exception:
            self._report_failure(action)
            return False

    def track_question(
        self,
        question_text: str,
        page: str = "CHAT"
    ) -> bool:
        """
        Records QUESTION_SUBMITTED.

        Called SERVER-SIDE from the chat route rather than from the
        browser: the backend already has the question, and emitting it
        here means the record cannot be skipped by a client that chooses
        not to send it. The ingest API still accepts QUESTION_SUBMITTED
        so the documented contract works, but the frontend deliberately
        does not send it — that would double-log every question.
        """
        return self.track(
            ActivityAction.QUESTION_SUBMITTED,
            page=page,
            question_text=question_text
        )

    def track_batch(
        self,
        events: Sequence[dict]
    ) -> dict:
        """
        Bulk ingest for the browser's flush queue — one DB round trip
        for a whole page session's worth of events.

        Returns {"accepted": n, "rejected": n} so the frontend can log a
        mismatch during development. Never raises, and never reports a
        partial failure as an error: dropped telemetry is not worth an
        error response to a user who is just navigating the UI.
        """
        result = {"accepted": 0, "rejected": 0}

        try:
            if not settings.ACTIVITY_TRACKING_ENABLED:
                result["rejected"] = len(events)
                return result

            # Hard cap so a malformed or hostile client cannot push an
            # unbounded insert through this endpoint.
            capped = list(events)[: settings.ACTIVITY_MAX_BATCH_SIZE]
            result["rejected"] += len(events) - len(capped)

            context = get_audit_context()
            entries = []

            for event in capped:
                entry = self._build_entry(
                    context,
                    action=event.get("action"),
                    page=event.get("page"),
                    from_page=event.get("from_page"),
                    feature=event.get("feature"),
                    question_text=event.get("question_text"),
                    target_type=event.get("target_type"),
                    target_id=event.get("target_id")
                )

                if entry is None:
                    result["rejected"] += 1
                else:
                    entries.append(entry)

            if entries:
                db = SessionLocal()
                try:
                    result["accepted"] = ActivityRepository(db).create_many(entries)
                finally:
                    db.close()

            return result

        except Exception:
            self._report_failure("batch")
            result["rejected"] = len(events)
            result["accepted"] = 0
            return result

    def _report_failure(self, action: str) -> None:
        try:
            logger.error(
                f"ACTIVITY WRITE FAILED for action={action!r} — "
                f"the user's request was NOT affected",
                exc_info=True,
                extra={"event": "activity_write_failed", "activity_action": action}
            )
        except Exception:
            pass


# Module-level singleton, same pattern as audit_service / cache_service.
activity_service = ActivityService()

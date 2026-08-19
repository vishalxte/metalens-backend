"""
Data access for activity_logs. Same shape as every other repository in
this project (constructor takes a Session, methods own their commit).

Activity arrives in batches from the browser, so create_many() is the
primary write path and create() is the exception.

The `time_spent_per_page()` and `feature_usage_counts()` aggregates were
removed along with the `duration_ms` column and the SESSION_START /
SESSION_END / FEATURE_USAGE actions — the table no longer carries the
data those queries summed.
"""
from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.activity_log import ActivityLog


class ActivityRepository:

    def __init__(
        self,
        db: Session
    ):
        self.db = db

    def create(
        self,
        activity_log: ActivityLog
    ) -> ActivityLog:

        self.db.add(activity_log)

        self.db.commit()

        self.db.refresh(activity_log)

        return activity_log

    def create_many(
        self,
        activity_logs: Sequence[ActivityLog]
    ) -> int:
        """
        Primary write path: the frontend flushes a queue of events in one
        request, so the whole batch lands in a single INSERT + commit
        rather than one round trip per page view.
        """
        if not activity_logs:
            return 0

        self.db.add_all(activity_logs)

        self.db.commit()

        return len(activity_logs)

    def _apply_filters(
        self,
        query,
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
        search: Optional[str] = None
    ):
        """
        Shared by search() and count() so a filtered page and its total
        can never disagree.
        """
        if action:
            query = query.filter(ActivityLog.action == action)

        if page:
            query = query.filter(ActivityLog.page == page)

        if feature:
            query = query.filter(ActivityLog.feature == feature)

        if user_id is not None:
            query = query.filter(ActivityLog.user_id == user_id)

        if user_email:
            query = query.filter(ActivityLog.user_email == user_email)

        if session_id:
            query = query.filter(ActivityLog.session_id == session_id)

        if target_type:
            query = query.filter(ActivityLog.target_type == target_type)

        if target_id:
            query = query.filter(ActivityLog.target_id == target_id)

        if start_date:
            query = query.filter(ActivityLog.event_time >= start_date)

        if end_date:
            query = query.filter(ActivityLog.event_time <= end_date)

        if search:
            # Free text over the question. This is the "which question
            # did the user ask?" lookup the redesign exists to serve.
            query = query.filter(ActivityLog.question_text.ilike(f"%{search}%"))

        return query

    def search(
        self,
        limit: int = 50,
        offset: int = 0,
        **filters
    ):
        query = self._apply_filters(
            self.db.query(ActivityLog),
            **filters
        )

        return (
            query
            .order_by(ActivityLog.event_time.desc(), ActivityLog.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def count(
        self,
        **filters
    ) -> int:
        query = self._apply_filters(
            self.db.query(func.count(ActivityLog.id)),
            **filters
        )

        return query.scalar() or 0

    def delete_older_than(
        self,
        cutoff: datetime
    ) -> int:
        """
        Retention purge (settings.ACTIVITY_RETENTION_DAYS — much shorter
        than audit retention, which is the practical reason these two
        live in separate tables).
        """
        deleted = (
            self.db.query(ActivityLog)
            .filter(ActivityLog.event_time < cutoff)
            .delete(synchronize_session=False)
        )

        self.db.commit()

        return deleted

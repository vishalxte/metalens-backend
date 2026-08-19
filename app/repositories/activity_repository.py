from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.activity_log import ActivityLog


class ActivityRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, activity_log: ActivityLog) -> ActivityLog:
        self.db.add(activity_log)
        self.db.commit()
        self.db.refresh(activity_log)
        return activity_log

    def create_many(self, activity_logs: Sequence[ActivityLog]) -> int:
        if not activity_logs:
            return 0
        self.db.add_all(activity_logs)
        self.db.commit()
        return len(activity_logs)

    def _apply_filters(self, query, action: Optional[str] = None, page: Optional[str] = None,
                       feature: Optional[str] = None, user_id: Optional[int] = None,
                       user_email: Optional[str] = None, session_id: Optional[str] = None,
                       target_type: Optional[str] = None, target_id: Optional[str] = None,
                       start_date: Optional[datetime] = None, end_date: Optional[datetime] = None):
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
        return query

    def _base_query(self, **filters):
        return self._apply_filters(self.db.query(ActivityLog), **filters)

    def search(self, limit=50, offset=0, search: Optional[str] = None, **filters):
        query = self._base_query(**filters)
        if not search:
            return query.order_by(ActivityLog.event_time.desc(), ActivityLog.id.desc()).offset(offset).limit(limit).all()
        rows = query.order_by(ActivityLog.event_time.desc(), ActivityLog.id.desc()).all()
        needle = search.casefold()
        matches = [row for row in rows if needle in (row.question_text or "").casefold()]
        return matches[offset:offset + limit]

    def count(self, search: Optional[str] = None, **filters) -> int:
        if not search:
            return self._apply_filters(self.db.query(func.count(ActivityLog.id)), **filters).scalar() or 0
        rows = self._base_query(**filters).all()
        needle = search.casefold()
        return sum(1 for row in rows if needle in (row.question_text or "").casefold())

    def delete_older_than(self, cutoff: datetime) -> int:
        deleted = self.db.query(ActivityLog).filter(ActivityLog.event_time < cutoff).delete(synchronize_session=False)
        self.db.commit()
        return deleted

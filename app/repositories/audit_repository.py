"""
Data access for audit_logs. Same shape as every other repository in this
project (constructor takes a Session, methods own their commit).

Two rules specific to this repository:
  1. No OTHER repository ever calls this one. Audit writes are initiated
     by business SERVICES via AuditService — a repository writing audit
     rows would tie the audit trail to the business transaction, which
     is exactly what must not happen.
  2. It exposes no update() and no delete-by-id. An audit trail is
     append-only; the only removal path is the retention purge below,
     which deletes wholesale by age.
"""
from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog


class AuditRepository:

    def __init__(
        self,
        db: Session
    ):
        self.db = db

    def create(
        self,
        audit_log: AuditLog
    ) -> AuditLog:

        self.db.add(audit_log)

        self.db.commit()

        self.db.refresh(audit_log)

        return audit_log

    def create_many(
        self,
        audit_logs: Sequence[AuditLog]
    ) -> Sequence[AuditLog]:
        """One commit for a batch of related events."""
        if not audit_logs:
            return []

        self.db.add_all(audit_logs)

        self.db.commit()

        return audit_logs

    def _apply_filters(
        self,
        query,
        category: Optional[str] = None,
        action: Optional[str] = None,
        status: Optional[str] = None,
        user_id: Optional[int] = None,
        user_email: Optional[str] = None,
        session_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        search: Optional[str] = None
    ):
        """
        Shared by search(), count() and the exporter so a filtered page,
        its total, and a CSV of the same filters can never disagree.
        Every filter is optional and applied only when supplied.
        """
        if category:
            query = query.filter(AuditLog.category == category)

        if action:
            query = query.filter(AuditLog.action == action)

        if status:
            query = query.filter(AuditLog.status == status)

        if user_id is not None:
            query = query.filter(AuditLog.user_id == user_id)

        if user_email:
            query = query.filter(AuditLog.user_email == user_email)

        if session_id:
            query = query.filter(AuditLog.session_id == session_id)

        if ip_address:
            query = query.filter(AuditLog.ip_address == ip_address)

        if target_type:
            query = query.filter(AuditLog.target_type == target_type)

        if target_id:
            query = query.filter(AuditLog.target_id == target_id)

        if start_date:
            query = query.filter(AuditLog.event_time >= start_date)

        if end_date:
            query = query.filter(AuditLog.event_time <= end_date)

        if search:
            # Free text across the human-readable columns. ILIKE is
            # adequate at audit-table selectivity because in practice it
            # is always combined with an indexed date range.
            pattern = f"%{search}%"
            query = query.filter(
                AuditLog.user_email.ilike(pattern)
                | AuditLog.action.ilike(pattern)
                | AuditLog.target_id.ilike(pattern)
            )

        return query

    def search(
        self,
        limit: int = 50,
        offset: int = 0,
        **filters
    ):
        """
        Newest-first page. Ordering matches the composite indexes on the
        model (dimension + event_time DESC), so a filtered page is an
        index scan rather than a sort over the whole table.
        """
        query = self._apply_filters(
            self.db.query(AuditLog),
            **filters
        )

        return (
            query
            .order_by(AuditLog.event_time.desc(), AuditLog.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def count(
        self,
        **filters
    ) -> int:
        query = self._apply_filters(
            self.db.query(func.count(AuditLog.id)),
            **filters
        )

        return query.scalar() or 0

    def get_by_id(
        self,
        audit_id: int
    ):
        return (
            self.db.query(AuditLog)
            .filter(AuditLog.id == audit_id)
            .first()
        )

    def iter_for_export(
        self,
        batch_size: int = 1000,
        max_rows: int = 100000,
        **filters
    ):
        """
        Streams matching rows in batches instead of materializing
        everything at once, so exporting a large date range does not
        load the whole result set into memory. Returns a generator;
        callers must consume it inside the session's lifetime.
        """
        query = self._apply_filters(
            self.db.query(AuditLog),
            **filters
        ).order_by(AuditLog.event_time.desc(), AuditLog.id.desc())

        yielded = 0

        for row in query.yield_per(batch_size):
            if yielded >= max_rows:
                break

            yield row
            yielded += 1

    def count_by_category(
        self,
        **filters
    ):
        """
        Rows per category for the given filters. Grouped in Postgres
        rather than in Python.
        """
        query = self._apply_filters(
            self.db.query(
                AuditLog.category,
                func.count(AuditLog.id).label("total")
            ),
            **filters
        )

        return (
            query
            .group_by(AuditLog.category)
            .order_by(func.count(AuditLog.id).desc())
            .all()
        )

    def delete_older_than(
        self,
        cutoff: datetime
    ) -> int:
        """
        Retention purge (settings.AUDIT_RETENTION_DAYS). The only delete
        path on this table, and age-based only — there is deliberately
        no way to delete one specific audit record, since that would
        make the trail forgeable.
        """
        deleted = (
            self.db.query(AuditLog)
            .filter(AuditLog.event_time < cutoff)
            .delete(synchronize_session=False)
        )

        self.db.commit()

        return deleted

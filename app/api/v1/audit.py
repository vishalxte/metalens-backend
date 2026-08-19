"""
Audit read API — Super Admin only.

Every route is gated by the EXISTING get_super_admin dependency, applied
at router level so a route added here later cannot ship without it.
Company Admins and regular Users have no access.

READ-ONLY on purpose. Audit rows are written exclusively by business
services through AuditService — there is no POST/PUT/DELETE here.
"""
import csv
import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_super_admin
from app.core.config import settings
from app.core.logging import logger
from app.database.session import get_db
from app.models.audit_log import (
    AuditCategory,
    AuditAction,
    AuditStatus,
    TargetType
)
from app.repositories.audit_repository import AuditRepository
from app.schemas.audit import (
    AuditLogResponse,
    AuditLogPage,
    AuditSummary,
    AuditCategoryCount,
    AuditMetadata
)

router = APIRouter(
    prefix="/audit",
    tags=["Audit"],
    dependencies=[Depends(get_super_admin)]
)


# Column order for CSV export. Declared once, at module level, so the
# export and any future XLSX/PDF exporter stay column-identical.
EXPORT_COLUMNS = (
    "id",
    "event_time",
    "category",
    "action",
    "status",
    "user_id",
    "user_email",
    "session_id",
    "ip_address",
    "target_type",
    "target_id",
    "details"
)


def _collect_filters(
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
) -> dict:
    """
    Bundles the query params into the kwargs AuditRepository expects.
    Kept as a plain function (not a Depends) so /logs, /summary and
    /export reuse the identical filter surface — that is what guarantees
    an export contains exactly the rows the screen showed.
    """
    return {
        "category": category,
        "action": action,
        "status": status,
        "user_id": user_id,
        "user_email": user_email,
        "session_id": session_id,
        "ip_address": ip_address,
        "target_type": target_type,
        "target_id": target_id,
        "start_date": start_date,
        "end_date": end_date,
        "search": search
    }


# ─────────────────────────────────────────
# Filter vocabulary — lets the UI build dropdowns from the server rather
# than duplicating the constants from app/models/audit_log.py.
# ─────────────────────────────────────────
@router.get("/metadata", response_model=AuditMetadata, status_code=200)
def get_audit_metadata():
    actions = sorted(
        value
        for name, value in vars(AuditAction).items()
        if not name.startswith("_") and isinstance(value, str)
    )

    return AuditMetadata(
        categories=list(AuditCategory.ALL),
        statuses=list(AuditStatus.ALL),
        actions=actions,
        target_types=list(TargetType.ALL)
    )


# ─────────────────────────────────────────
# Paginated, filterable audit log listing.
# ─────────────────────────────────────────
@router.get("/logs", response_model=AuditLogPage, status_code=200)
def list_audit_logs(
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1),
    offset: int = Query(default=0, ge=0),
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
    Newest-first. `limit` is clamped rather than rejected, so a client
    asking for too much gets a valid (smaller) page instead of a 422.
    """
    limit = min(limit, settings.AUDIT_MAX_PAGE_SIZE)

    filters = _collect_filters(
        category, action, status, user_id, user_email, session_id,
        ip_address, target_type, target_id, start_date, end_date, search
    )

    repo = AuditRepository(db)

    return AuditLogPage(
        total=repo.count(**filters),
        limit=limit,
        offset=offset,
        items=repo.search(limit=limit, offset=offset, **filters)
    )


# ─────────────────────────────────────────
# Aggregate counts — the data behind a dashboard panel.
# ─────────────────────────────────────────
@router.get("/summary", response_model=AuditSummary, status_code=200)
def get_audit_summary(
    db: Session = Depends(get_db),
    category: Optional[str] = None,
    action: Optional[str] = None,
    status: Optional[str] = None,
    user_id: Optional[int] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
):
    filters = _collect_filters(
        category=category,
        action=action,
        status=status,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date
    )

    repo = AuditRepository(db)

    return AuditSummary(
        total=repo.count(**filters),
        by_category=[
            AuditCategoryCount(category=row.category, total=row.total)
            for row in repo.count_by_category(**filters)
        ]
    )


# ─────────────────────────────────────────
# CSV export, streamed row-by-row over the SAME filter surface.
# ─────────────────────────────────────────
@router.get("/export", status_code=200)
def export_audit_logs(
    db: Session = Depends(get_db),
    format: str = Query(default="csv", pattern="^(csv)$"),
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
    filters = _collect_filters(
        category, action, status, user_id, user_email, session_id,
        ip_address, target_type, target_id, start_date, end_date, search
    )

    repo = AuditRepository(db)

    def generate():
        """
        Streamed rather than built in memory, so exporting a year of
        audit history doesn't hold the whole table in RAM.
        """
        buffer = io.StringIO()
        writer = csv.writer(buffer)

        writer.writerow(EXPORT_COLUMNS)
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)

        for row in repo.iter_for_export(
            max_rows=settings.AUDIT_EXPORT_MAX_ROWS,
            **filters
        ):
            writer.writerow([
                getattr(row, column) if column != "details"
                else (row.details or "")
                for column in EXPORT_COLUMNS
            ])
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)

    filename = f"audit_logs_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"

    logger.info(
        "Audit export requested",
        extra={"event": "audit_export_requested"}
    )

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


# ─────────────────────────────────────────
# Single audit record. Registered LAST so the literal paths above are
# never swallowed by this path parameter.
# ─────────────────────────────────────────
@router.get("/logs/{audit_id}", response_model=AuditLogResponse, status_code=200)
def get_audit_log(
    audit_id: int,
    db: Session = Depends(get_db)
):
    entry = AuditRepository(db).get_by_id(audit_id)

    if not entry:
        raise HTTPException(status_code=404, detail="Audit log not found")

    return entry

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.repositories.user_repository import UserRepository
from app.schemas.company_user import (
    CompanyUserCreate,
    CompanyUserUpdate,
    CompanyUserResponse
)
from app.api.dependencies.auth import get_company_admin
from app.core.security import hash_password
from app.models.user import User, Role

# Audit integration note:
# Per the agreed scope, this module is NOT refactored — the existing
# business logic, method signatures, responses and status codes are left
# exactly as they were. Because these four routes hold their logic
# inline (there is no CompanyUserService to place the calls in), the
# AuditService calls are made here directly. This is the one documented
# exception to the "only business services call AuditService" rule, and
# it was chosen deliberately over extracting a service, since extraction
# would mean moving working code for no functional gain.
from app.models.audit_log import AuditAction, AuditStatus
from app.models.user_session import SessionEndReason
from app.services.audit_service import audit_service
from app.services.session_service import session_service

router = APIRouter(
    prefix="/company/users",
    tags=["Company Users"]
)


# ─────────────────────────────────────────
# A Customer (company admin, role=CUSTOMER) creates a User (role=USER)
# under their own tenant. Per spec: "A Customer can ... Manage their own
# Users" / "Users cannot exist without a Customer".
# ─────────────────────────────────────────
@router.post("", response_model=CompanyUserResponse, status_code=201)
def create_company_user(
    payload: CompanyUserCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_company_admin)
):
    repo = UserRepository(db)

    if repo.get_by_email(payload.email):
        audit_service.log_user_event(
            action=AuditAction.USER_CREATED,
            target_email=payload.email,
            status=AuditStatus.FAILURE,
            details={"reason": "email_already_exists"}
        )
        raise HTTPException(status_code=400, detail="A user with this email already exists")

    user = User(
        full_name=payload.full_name,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        role=Role.USER,
        customer_id=current_user.customer_id
    )

    created = repo.create(user)

    audit_service.log_user_event(
        action=AuditAction.USER_CREATED,
        target_user_id=created.id,
        target_email=created.email,
        details={
            "assigned_role": Role.USER,
            "assigned_customer_id": current_user.customer_id,
            "created_via": "company_admin"
        }
    )

    # Return value is unchanged — `created` is the same object repo.create()
    # returned before this audit call was added.
    return created


# ─────────────────────────────────────────
# List Users belonging to the current Customer's own company.
# ─────────────────────────────────────────
@router.get("", response_model=list[CompanyUserResponse], status_code=200)
def list_company_users(
    db: Session = Depends(get_db),
    current_user=Depends(get_company_admin)
):
    repo = UserRepository(db)
    return repo.get_all_for_customer(current_user.customer_id)


# ─────────────────────────────────────────
# Update a User belonging to the current Customer's own company.
# ─────────────────────────────────────────
@router.put("/{user_id}", response_model=CompanyUserResponse, status_code=200)
def update_company_user(
    user_id: int,
    payload: CompanyUserUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_company_admin)
):
    repo = UserRepository(db)
    user = repo.get_by_id_for_customer(user_id, current_user.customer_id)

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    changes = payload.model_dump(exclude_unset=True)

    # Captured BEFORE the update so the audit record can show the actual
    # transition, which is the whole point of auditing an activation or
    # deactivation rather than just recording "updated".
    previous_is_active = user.is_active

    updated = repo.update(user, **changes)

    # An Active<->Inactive flip is audited as its own event, not as a
    # generic update — "Deactivate User" / "Activate User" are separate
    # required events.
    if "is_active" in changes and changes["is_active"] != previous_is_active:
        # Deactivating an account already blocks the NEXT login, and
        # get_current_user() already rejects an inactive user — but that
        # check only fires on a fresh request. Closing the live sessions
        # too makes the deactivation immediate and, more importantly,
        # leaves a record of it: the session row shows ACCOUNT_DISABLED
        # rather than silently going quiet.
        if not changes["is_active"]:
            session_service.end_all_for_user(
                updated.id,
                reason=SessionEndReason.ACCOUNT_DISABLED,
                revoked_by_user_id=current_user.id
            )

        audit_service.log_user_event(
            action=(
                AuditAction.USER_ACTIVATED
                if changes["is_active"]
                else AuditAction.USER_DEACTIVATED
            ),
            target_user_id=updated.id,
            target_email=updated.email,
            details={
                "previous_is_active": previous_is_active,
                "new_is_active": changes["is_active"]
            }
        )

    # Only the CHANGED FIELD NAMES are recorded, never the values —
    # this keeps the record useful without turning the audit table into
    # a shadow copy of the users table.
    other_fields = [key for key in changes if key != "is_active"]

    if other_fields:
        audit_service.log_user_event(
            action=AuditAction.USER_UPDATED,
            target_user_id=updated.id,
            target_email=updated.email,
            details={"updated_fields": other_fields}
        )

    return updated


# ─────────────────────────────────────────
# Delete a User belonging to the current Customer's own company.
# ─────────────────────────────────────────
@router.delete("/{user_id}", status_code=200)
def delete_company_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_company_admin)
):
    repo = UserRepository(db)
    user = repo.get_by_id_for_customer(user_id, current_user.customer_id)

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Captured before the delete call: UserRepository.delete() is a SOFT
    # delete (is_active=False), so the row survives — but reading these
    # first keeps the audit record correct regardless of that.
    target_id = user.id
    target_email = user.email

    repo.delete(user)

    # Soft delete == is_active False, so the same reasoning as the
    # deactivate branch above applies: end the live sessions so the
    # removal takes effect now rather than whenever the token expires.
    session_service.end_all_for_user(
        target_id,
        reason=SessionEndReason.ACCOUNT_DISABLED,
        revoked_by_user_id=current_user.id
    )

    audit_service.log_user_event(
        action=AuditAction.USER_DELETED,
        target_user_id=target_id,
        target_email=target_email,
        details={
            "delete_type": "soft",
            "deleted_via": "company_admin"
        }
    )

    return {"message": f"User '{user.email}' deleted successfully"}

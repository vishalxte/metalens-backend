from fastapi import APIRouter
from fastapi import Depends

from sqlalchemy.orm import Session

from app.database.session import get_db

from app.schemas.auth import (
    LoginResponse,
    UserCreate,
    UserLogin,
    TokenResponse,VerifyOTP,ResendOTPResponse
)

from app.repositories.user_repository import UserRepository
from app.services.otp_service import resend_otp

from app.services.auth_service import AuthService

from app.api.dependencies.auth import get_super_admin, get_current_user

from app.models.audit_log import AuditAction, AuditStatus
from app.models.user_session import SessionEndReason
from app.services.audit_service import audit_service
from app.services.session_service import session_service
from app.core.request_context import get_session_id

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"]
)


# There is no public self-signup in the multi-tenant design: regular user
# only ever get created automatically when a Super Admin creates a
# Customer (see /customers). This endpoint is kept only for a Super Admin
# to create additional internal/admin-role accounts, hence it now requires
# an existing Super Admin token instead of being open.
@router.post("/register", dependencies=[Depends(get_super_admin)])
def register(
    payload: UserCreate,
    db: Session = Depends(get_db)
):

    repo = UserRepository(db)

    service = AuthService(repo)

    return service.register(
        payload.full_name,
        payload.email,
        payload.password,
        role=payload.role,
        customer_id=payload.customer_id
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: UserLogin,
    db: Session = Depends(get_db)
):

    repo = UserRepository(db)

    service = AuthService(repo)

    return await service.login(
        db,
        payload.email,
        payload.password,
        
    )



@router.post("/verify-otp", response_model=TokenResponse)
async def verify_otp(
    payload: VerifyOTP,
    db: Session = Depends(get_db)
):
    repo = UserRepository(db)

    service = AuthService(repo)

    return await service.verify_otp(
        db,
        payload.email,
        payload.otp
    )

@router.post("/resend-otp", response_model=ResendOTPResponse)
async def resend_otp_endpoint(
    email: str,
    db: Session = Depends(get_db)
):
    return await resend_otp(email,db)

# ─────────────────────────────────────────
# Logout.
#
# The JWT itself remains stateless and untouched — no signing change, no
# blacklist of raw tokens, no change to how a token is verified. What
# this does is close the `user_sessions` row identified by the token's
# `jti`, which get_current_user() then reads on the next request and
# rejects.
#
# So the token is not "invalidated" cryptographically; it is marked as
# belonging to a session that has ended. That is the standard way to add
# revocation to a stateless-JWT design without giving up stateless
# verification.
#
# The frontend's existing client-side logout (clearing localStorage in
# AuthContext) keeps working exactly as before, and remains what the
# user actually experiences. This just makes the logout stick
# server-side too.
# ─────────────────────────────────────────
@router.post("/logout", status_code=200)
def logout(
    current_user=Depends(get_current_user)
):
    # The authoritative jti, put into context by get_current_user().
    session_id = get_session_id()

    # Never raises; returns False for a token with no `jti` or no
    # session row (e.g. issued before this feature).
    session_service.end(
        session_id,
        reason=SessionEndReason.LOGOUT
    )

    audit_service.log_auth(
        action=AuditAction.LOGOUT,
        status=AuditStatus.SUCCESS,
        user_id=current_user.id,
        user_email=current_user.email
    )

    # Response body unchanged.
    return {"message": "Logged out successfully"}

from jose import jwt
from jose import JWTError

from fastapi import Depends
from fastapi import HTTPException
from fastapi.security import HTTPBearer

from sqlalchemy.orm import Session

from app.database.session import get_db
from app.repositories.user_repository import UserRepository
from app.repositories.customer_repository import CustomerRepository
from app.core.config import settings
from app.core.logging import logger
from app.core.request_context import set_current_user, set_session_id
from app.models.user import Role
from app.models.audit_log import AuditAction, AuditStatus
from app.models.user_session import SessionEndReason
from app.services.audit_service import audit_service
from app.services.session_service import session_service

security = HTTPBearer()


def _resolve_super_admin_id(user, db: Session):
    """
    Which Super Admin's tenant this request belongs to, for audit/log
    tracing purposes:
      - The caller IS the Super Admin -> their own id.
      - The caller is a tenant user (CUSTOMER/ADMIN/USER) -> the id of
        the Super Admin who created their Customer row
        (Customer.created_by).
      - No customer_id and not a Super Admin (shouldn't normally
        happen), or a legacy Customer row with no created_by recorded
        -> None, logged as "-".
    """
    if user.role == Role.SUPER_ADMIN:
        return user.id

    if user.customer_id is None:
        return None

    customer = CustomerRepository(db).get_by_id(user.customer_id)
    return customer.created_by if customer else None


def get_current_user(
    token=Depends(security),
    db: Session = Depends(get_db)
):
    try:
        payload = jwt.decode(
            token.credentials,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )

        email = payload.get("sub")

        # The token's `jti` (RFC 7519) is this login's session id. Set
        # into the request context immediately after a successful decode
        # — before any of the rejection branches below — so even a
        # request that gets refused still writes its SECURITY audit row
        # under the correct, server-issued session id.
        #
        # This deliberately overwrites whatever the client sent in the
        # X-Session-ID header: a signed claim outranks a client value.
        # Tokens issued before this feature have no `jti`; set_session_id
        # treats None as a no-op and everything continues to work.
        set_session_id(payload.get("jti"))

        if not email:
            logger.warning("Auth failed: JWT payload had no 'sub' (email) claim")
            # SECURITY audit: a structurally valid but malformed token.
            # Logged here rather than in a route because no route is ever
            # reached — this dependency is where the request dies.
            audit_service.log_security(
                action=AuditAction.INVALID_TOKEN,
                status=AuditStatus.FAILURE,
                details={"reason": "jwt_missing_sub_claim"}
            )
            raise HTTPException(
                status_code=401,
                detail="Invalid token"
            )

        repo = UserRepository(db)
        user = repo.get_by_email(email)

        if not user:
            logger.warning(f"Auth failed: token email {email!r} has no matching user")
            # A validly-signed token for a user that no longer exists is
            # worth flagging — it can mean a deleted account or a leaked
            # signing key being used against stale claims.
            audit_service.log_security(
                action=AuditAction.AUTHENTICATION_FAILURE,
                status=AuditStatus.FAILURE,
                user_email=email,
                details={"reason": "token_user_not_found"}
            )
            raise HTTPException(
                status_code=401,
                detail="User not found"
            )

        if not user.is_active:
            logger.warning(
                f"Auth rejected: deactivated user attempted access — email={email!r}"
            )
            audit_service.log_security(
                action=AuditAction.INACTIVE_ACCOUNT_ACCESS,
                status=AuditStatus.FAILURE,
                user_email=email,
                details={"reason": "account_deactivated", "role": user.role}
            )
            raise HTTPException(
                status_code=401,
                detail="Your account has been deactivated. Please contact your administrator."
            )

        # Tags every subsequent log line in this request with who made it,
        # via contextvars — so RAG/cache/LLM/DB logs downstream can all be
        # traced back to this user without threading it through every call.
        # Revocation gate. This is the one thing a stateless token
        # cannot do on its own: a token stays cryptographically valid
        # until `exp`, so "log this person out NOW" requires a
        # server-side record to check against.
        #
        # FAILS OPEN by design — returns None (proceed) when session
        # tracking is off, the token has no `jti`, no session row
        # exists, or the lookup itself errored. Only an explicitly read,
        # non-NULL `ended_at` reaches this branch, so a database blip
        # can never log out the entire deployment.
        end_reason = session_service.check_and_touch(payload.get("jti"))

        if end_reason:
            audit_service.log_security(
                action=AuditAction.INVALID_TOKEN,
                status=AuditStatus.FAILURE,
                user_email=user.email,
                details={
                    "reason": "session_terminated",
                    "end_reason": end_reason,
                    "role": user.role
                }
            )

            # Message is tailored so the user understands why they were
            # bounced, without leaking who revoked it.
            detail = (
                "You have been signed out by an administrator. Please log in again."
                if end_reason == SessionEndReason.REVOKED
                else "Your session has ended. Please log in again."
            )

            raise HTTPException(
                status_code=401,
                detail=detail
            )

        super_admin_id = _resolve_super_admin_id(user, db)
        # user_id/role are passed as keyword arguments so the original
        # positional signature is untouched — they feed AuditService and
        # ActivityService, which read the actor from context rather than
        # having it threaded through every call.
        set_current_user(
            user.email,
            user.customer_id,
            super_admin_id,
            user_id=user.id,
            role=user.role
        )

        logger.debug(
            f"Authenticated request — email={user.email!r}, role={user.role!r}, "
            f"customer_id={user.customer_id!r}, super_admin_id={super_admin_id!r}"
        )

        return user

    except JWTError:
        logger.warning("Auth failed: JWT decode error (invalid/expired/tampered token)")
        # Covers the "JWT Failure" auth event and the "Invalid Token"
        # security event in one record — the token never decoded, so
        # there is no user to attribute it to beyond the network context
        # the middleware already captured (IP, user agent, path).
        audit_service.log_security(
            action=AuditAction.JWT_FAILURE,
            status=AuditStatus.FAILURE,
            details={"reason": "jwt_decode_error"}
        )
        raise HTTPException(
            status_code=401,
            detail="Invalid token"
        )


def get_super_admin(
    current_user=Depends(get_current_user)
):
    if current_user.role != Role.SUPER_ADMIN:
        logger.warning(
            f"get_super_admin 403 — email={current_user.email!r}, role={current_user.role!r}"
        )
        # Authenticated but not authorized — the "403 / Permission
        # Denied" security event. The actor IS known here (auth already
        # succeeded), so it comes from context automatically.
        audit_service.log_security(
            action=AuditAction.PERMISSION_DENIED,
            status=AuditStatus.FAILURE,
            details={
                "required": "SUPER_ADMIN",
                "actual_role": current_user.role,
                "gate": "get_super_admin"
            }
        )
        raise HTTPException(
            status_code=403,
            detail="Access denied. Super admin only."
        )
    return current_user


def get_customer_user(
    current_user=Depends(get_current_user)
):
    """
    Gate for every tenant-scoped, chat-facing endpoint: chat, search,
    conversations. Both the company admin (role=CUSTOMER) and its regular
    staff (role=USER) can chat, so this only checks that the caller
    belongs to *some* tenant. The Super Admin is tenant-less
    (customer_id is NULL) and must never reach chat/search/conversations
    per the spec ("Super Admin should never access Customer or User
    chats") — that's enforced here, since a Super Admin always fails this
    check.
    """
    if current_user.customer_id is None:
        logger.warning(
            f"get_customer_user 403 — email={current_user.email!r}, "
            f"role={current_user.role!r}, customer_id={current_user.customer_id!r}"
        )
        audit_service.log_security(
            action=AuditAction.PERMISSION_DENIED,
            status=AuditStatus.FAILURE,
            details={
                "required": "tenant-scoped account",
                "actual_role": current_user.role,
                "gate": "get_customer_user"
            }
        )
        raise HTTPException(
            status_code=403,
            detail="Access denied. This action requires a customer account."
        )
    return current_user


def get_company_admin(
    current_user=Depends(get_current_user)
):
    """
    Gate for document management (upload/delete/list) and company-user
    management (create/update/delete/view Users). Only role=CUSTOMER (the
    one default user auto-created for a Company/tenant) is allowed —
    role=USER accounts are chat-only and must never upload/delete
    documents or manage other users, per spec.
    """
    if current_user.role != Role.CUSTOMER or current_user.customer_id is None:
        logger.warning(
            f"get_company_admin 403 — email={current_user.email!r}, "
            f"role={current_user.role!r}, customer_id={current_user.customer_id!r}"
        )
        audit_service.log_security(
            action=AuditAction.PERMISSION_DENIED,
            status=AuditStatus.FAILURE,
            details={
                "required": "CUSTOMER",
                "actual_role": current_user.role,
                "gate": "get_company_admin"
            }
        )
        raise HTTPException(
            status_code=403,
            detail="Access denied. This action requires the company admin account."
        )
    return current_user

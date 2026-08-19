from datetime import datetime

from sqlalchemy.orm import Session


from app.models.user import User, Role
from app.repositories.user_repository import UserRepository
from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    new_session_id
)
from app.core.exceptions import (
    IncorrectOtpException,
    UserAlreadyExistsException,
    InvalidCredentialsException,
    UserAccountDeactivatedException,
    OtpDeliveryException
)
from app.core.config import settings
from app.core.logging import logger
from app.services.otp_service import send_otp,  store_otp
from app.models.audit_log import AuditAction, AuditStatus
from app.services.audit_service import audit_service
from app.services.session_service import session_service
from app.core.request_context import set_session_id
from app.core.permissions import permissions_claim







from app.models.otp import OTP

class AuthService:
    """
    NOTE: there is no public self-signup in the multi-tenant design.
    Users are created only by:
      1. Super Admin (seeded once)
      2. CustomerService when a new Customer is created.
    """

    def __init__(self, repository: UserRepository):
        self.repository = repository

    def register(
        self,
        full_name,
        email,
        password,
        role=Role.CUSTOMER,
        customer_id=None,
    ):
        existing_user = self.repository.get_by_email(email)

        if existing_user:
            raise UserAlreadyExistsException()

        user = User(
            full_name=full_name,
            email=email,
            hashed_password=hash_password(password),
            role=role,
            customer_id=customer_id,
        )

        created = self.repository.create(user)

        logger.info(f"User registered — email={email!r}, role={role!r}")


        # USER_MANAGEMENT audit. The ACTOR (whoever is holding the Super
        # Admin token) comes from the request context; the TARGET is the
        # user just created. Password is never passed in — only the
        # non-secret attributes that make the record meaningful.
        audit_service.log_user_event(
            action=AuditAction.USER_CREATED,
            target_user_id=created.id,
            target_email=created.email,
            details={
                "assigned_role": role,
                "assigned_customer_id": customer_id,
                "created_via": "auth_service.register"
            }
        )

        return created

    async def login(self,db: Session, email: str, password: str):
        user = self.repository.get_by_email(email)

        if not user:

            # Deliberately the same exception/message as a wrong password
            # (InvalidCredentialsException, logged by main.py's handler)
            # so a failed login never reveals whether the email exists.
            #
            # The audit trail, however, DOES distinguish the two cases —
            # it's internal and only a Super Admin can read it, and
            # telling "unknown email" apart from "wrong password" is
            # exactly what makes credential-stuffing visible. The
            # attempted email is recorded via the override because
            # authentication never ran, so the request context is empty.
            audit_service.log_auth(
                action=AuditAction.LOGIN_FAILED,
                status=AuditStatus.FAILURE,
                user_email=email,
                details={"reason": "unknown_email"}
            )
            raise InvalidCredentialsException()

        if not verify_password(password, user.hashed_password):
            audit_service.log_auth(
                action=AuditAction.LOGIN_FAILED,
                status=AuditStatus.FAILURE,
                user_id=user.id,
                user_email=email,
                details={"reason": "invalid_password"}
            )
            raise InvalidCredentialsException()


        if not user.is_active:
            raise UserAccountDeactivatedException()

        # SMTP is a third-party dependency on the authentication path, so
        # its failure has to be handled rather than allowed to propagate.
        # Unguarded, a rejected credential or an unreachable mail server
        # produced an unhandled exception: HTTP 500 with a stack trace
        # leaked to the client, nothing in the audit trail, and every
        # single user locked out for as long as the outage lasted.
        #
        # Note the ORDER is already correct and is preserved: send first,
        # store second. An OTP that was never delivered is therefore never
        # written to the database, so a failed send leaves no row a
        # subsequent attempt could collide with or that anyone could
        # guess against.
        try:
            otp = await send_otp(email)
        except Exception as exc:
            # SECURITY category, not AUTHENTICATION: the credentials were
            # correct and the user did nothing wrong. Filing this as a
            # login failure would make a mail outage look like an attack.
            audit_service.log_security(
                action=AuditAction.OTP_SEND_FAILED,
                status=AuditStatus.FAILURE,
                user_email=email,
                details={
                    # Type only. The SMTP message can echo back the
                    # configured username and other connection detail,
                    # which must not land in the audit table.
                    "error_type": type(exc).__name__,
                    "smtp_host": settings.SMTP_HOST
                }
            )

            # Full detail goes to the application log, where operators
            # need it to diagnose the outage.
            logger.error(
                f"OTP delivery failed for {email!r} via {settings.SMTP_HOST} "
                f"— login cannot complete",
                exc_info=True,
                extra={"event": "otp_send_failed"}
            )

            raise OtpDeliveryException()

        # Return value captured so its id can be recorded on both audit
        # rows — the correlation handle described below.
        otp_entry = store_otp(db, email, otp)

        # NOTE ON WHERE THE TOKEN AND SESSION ARE CREATED
        # ───────────────────────────────────────────────
        # Deliberately NOT here. Passing the password is only the first
        # factor; the caller is not authenticated until the OTP is
        # verified, and this method issues no token — it returns an
        # "otp_required" challenge.
        #
        # An earlier version did mint a token, open a user_sessions row
        # and write a LOGIN SUCCESS audit event at this point, then threw
        # the token away and returned the challenge. That produced an
        # orphan session row for every login attempt (a session whose
        # token nobody ever received), while the token the user actually
        # ends up holding — issued in verify_otp() — had no session row
        # at all, so it could not be revoked or listed as active.
        #
        # All of that now lives in verify_otp(), which is the one place
        # a usable token is handed out.
        logger.info(f"OTP sent for login — email={email!r}, role={user.role!r}")

        # First factor passed and a second factor was dispatched. Audited
        # as its own event, NOT as LOGIN: recording a successful login
        # before the OTP is checked would mean the trail shows a
        # completed authentication that may never happen.
        #
        # session_id is NULL on this row and that is correct — no session
        # exists until verify_otp() issues a token. To keep the two
        # halves of one login attempt joinable anyway, the OTP's own id
        # is recorded here and again on the LOGIN row, so a reader can
        # match "code sent at 12:38:46" to "logged in at 12:39:00"
        # without inventing a session that did not exist yet.
        audit_service.log_auth(
            action=AuditAction.OTP_SENT,
            status=AuditStatus.SUCCESS,
            user_id=user.id,
            user_email=user.email,
            details={
                "stage": "password_verified_otp_dispatched",
                # NOT the code itself — only its row id. The code is a
                # live credential for 5 minutes and must never be
                # written to the audit trail.
                "otp_id": getattr(otp_entry, "id", None)
            }
        )

        # Response shape unchanged.
        return {
            "message": "OTP sent successfully",
            "otp_required": True,
        }

    

        


        # Optional: Delete OTP after successful verification
    async def verify_otp(
    self,
    db: Session,
    email: str,
    otp: str,
):
    # Find OTP
     otp_entry = (
        db.query(OTP)
        .filter(
            OTP.email == email,
            OTP.otp == otp
        )
        .first()
    )

    # OTP not found
     if otp_entry is None:
        # A wrong or replayed code is a security-relevant event — this
        # is what brute-forcing the second factor looks like in the trail.
        audit_service.log_auth(
            action=AuditAction.OTP_FAILED,
            status=AuditStatus.FAILURE,
            user_email=email,
            details={"reason": "incorrect_otp"}
        )
        raise IncorrectOtpException()

    # OTP expired
     if otp_entry.expires_at < datetime.utcnow():
        db.delete(otp_entry)
        db.commit()
        audit_service.log_auth(
            action=AuditAction.OTP_FAILED,
            status=AuditStatus.FAILURE,
            user_email=email,
            details={"reason": "otp_expired"}
        )
        raise InvalidCredentialsException("OTP expired")

    # Fetch user
     user = (
        db.query(User)
        .filter(User.email == email)
        .first()
    )

     if user is None:
        audit_service.log_auth(
            action=AuditAction.OTP_FAILED,
            status=AuditStatus.FAILURE,
            user_email=email,
            details={"reason": "user_not_found"}
        )
        raise InvalidCredentialsException("User not found")

    # Read before the delete — the row is gone afterwards, and this id
    # is what links this LOGIN back to the OTP_SENT event that started
    # the attempt (OTP_SENT has no session_id, because no session
    # existed yet).
     matched_otp_id = otp_entry.id

    # Delete OTP
     db.delete(otp_entry)
     db.commit()

    # ── Authentication is complete only at this point ────────────────
    # Both factors have now passed, so this is where the session begins
    # and where the only usable token is issued. Everything below was
    # moved here from login(); see the note there for why.

    # Generated here rather than letting create_access_token() default
    # it, because this method needs the value: it goes into the token as
    # `jti` AND becomes the primary key of the user_sessions row, which
    # is what makes the token revocable.
     session_id = new_session_id()

    # RFC 7519 claim set. `sub`, `role` and `customer_id` are unchanged
    # from before; `exp` and `iat` are added by create_access_token.
    #
    # `permissions` is derived from the role at issue time (see
    # app/core/permissions.py). Enforcement stays on the existing role
    # dependencies — require_permission() re-derives server-side rather
    # than trusting the array, per OWASP ASVS V4.
     token = create_access_token(
        {
            "sub": user.email,
            "role": user.role,
            "permissions": permissions_claim(user.role),
            "customer_id": user.customer_id,
            "jti": session_id,
        }
    )

    # Opens the revocable server-side record. Never raises — a failure
    # is logged and the login still succeeds (see SessionService).
     session_service.start(
        session_id=session_id,
        user_id=user.id,
        user_email=user.email,
        role=user.role,
        customer_id=user.customer_id
    )

    # Tags the rest of THIS request with the new session id so the LOGIN
    # audit row below carries it. get_current_user() never runs on this
    # endpoint — the caller has no token yet — so without this the login
    # would be the one event in the session that isn't correlated to it.
     set_session_id(session_id)

     logger.info(f"Login successful (OTP verified) — email={email!r}, role={user.role!r}")

    # The token itself is never recorded — only that one was issued, to
    # whom, and with what role.
     audit_service.log_auth(
        action=AuditAction.LOGIN,
        status=AuditStatus.SUCCESS,
        user_id=user.id,
        user_email=user.email,
        details={
            "is_active": user.is_active,
            "second_factor": "OTP",
            # Joins this row back to the OTP_SENT event for the same
            # attempt. From there the whole login is traceable: OTP_SENT
            # -> LOGIN -> everything under this session_id.
            "otp_id": matched_otp_id
        }
    )

    # Response shape unchanged.
     return {
        "access_token": token,
        "token_type": "bearer",
        "role": user.role,
        "customer_id": user.customer_id,
    }

    
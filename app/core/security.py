import uuid

from datetime import datetime
from datetime import timedelta

from jose import jwt

from passlib.context import CryptContext

from app.core.config import settings


pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"
)


def hash_password(password: str):

    return pwd_context.hash(password)


def verify_password(
    plain_password,
    hashed_password
):

    return pwd_context.verify(
        plain_password,
        hashed_password
    )


def new_session_id() -> str:
    """
    Generates a session identifier for a new login.

    This becomes the token's `jti` claim (RFC 7519 "JWT ID") and, from
    the audit framework's point of view, IS the session id. It is
    generated server-side and travels inside the SIGNED token, so unlike
    a client-supplied `X-Session-ID` header it cannot be forged,
    replayed under another identity, or collided with on purpose — which
    is the whole reason an audit trail can rely on it for
    non-repudiation.

    Exposed separately from create_access_token() so the caller
    (AuthService.login) knows the id it just issued and can open the
    matching `user_sessions` row with it.
    """
    return str(uuid.uuid4())


def create_access_token(data: dict):
    """
    Signature deliberately unchanged — still one positional dict.

    `jti` and `iat` are added here only if the caller did not already
    supply them, so:
      - AuthService can pass its own `jti` (it needs to know the value
        to create the session row), and
      - any other/older caller automatically still gets a unique `jti`
        without having to change a single line.

    `iat` (issued-at) is standard alongside `jti` and gives the audit
    trail an authoritative token-issue time that doesn't depend on the
    session row existing.
    """
    expire = datetime.utcnow() + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )

    payload = data.copy()

    payload.update({"exp": expire})

    payload.setdefault("jti", new_session_id())
    payload.setdefault("iat", datetime.utcnow())

    return jwt.encode(
        payload,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM
    )
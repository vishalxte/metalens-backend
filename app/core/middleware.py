"""
Request-lifecycle logging middleware — the single place that gives us:
  - a unique request_id per request (generated, or reused from an
    incoming X-Request-ID header if a caller/gateway already set one),
    propagated via contextvars so every log line anywhere in the request
    is automatically tagged with it (see app/core/request_context.py);
  - one INFO log when a request starts (method, path, client, query
    params — never the body, so passwords/tokens in login/register
    payloads never end up in logs);
  - one INFO log when it finishes, with HTTP status code and total
    execution time in milliseconds — the "request completion with status
    code and total execution time" requirement;
  - a top-level safety net that logs the full stack trace of any
    unhandled exception (with request_id attached) before re-raising, so
    it still reaches FastAPI's normal exception handling / the default
    500 response — this middleware only *observes*, it never changes
    what the client receives.
"""
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.logging import logger
from app.core.request_context import (
    init_request_context,
    set_request_id,
    set_request_meta
)

REQUEST_ID_HEADER = "X-Request-ID"

# Browser tab correlation id, if the frontend activity tracker sends one.
#
# UNTRUSTED, AND NEVER THE SESSION ID. The enterprise session id is the
# JWT's `jti` — server-generated (UUID v4), embedded in the signed token,
# and therefore unforgeable. A client-supplied value can be invented,
# replayed under another identity, or deliberately collided with, so
# accepting one here would be a session-fixation vector: an attacker
# could pre-seed a session id and have the victim's audit rows filed
# under it.
#
# This header is therefore read ONLY into `client_session_id`, which is
# used for nothing but grouping a browser tab's activity events, and is
# stored as `activity_logs.metadata_json.client_session_id`.
CLIENT_SESSION_ID_HEADER = "X-Session-ID"

# Set by reverse proxies / load balancers. Checked before
# request.client.host so the real client IP is recorded rather than the
# proxy's, which is what a security audit trail actually needs.
FORWARDED_FOR_HEADER = "X-Forwarded-For"

# Audit stores user_agent in a VARCHAR(512); truncate here rather than
# letting an absurdly long header blow up the INSERT.
_MAX_USER_AGENT_LEN = 512


def _resolve_client_ip(request: Request) -> str:
    """
    X-Forwarded-For is a comma-separated chain ("client, proxy1, proxy2")
    — the left-most entry is the original client.
    """
    forwarded = request.headers.get(FORWARDED_FOR_HEADER)

    if forwarded:
        return forwarded.split(",")[0].strip()

    return request.client.host if request.client else "-"


class RequestLoggingMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next):
        # Installs a fresh context dict for this request. Must be the
        # FIRST thing that happens: this runs in the async request task,
        # which is the ancestor context of every sync dependency and
        # route handler FastAPI later dispatches to a worker thread —
        # so they all end up sharing this one dict and can write back
        # into it. See app/core/request_context.py for the full
        # explanation of why a shared dict is required here.
        init_request_context()

        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        set_request_id(request_id)

        # Capture the network/browser fingerprint into the request context
        # before routing, so AuditService can attach it to any event later
        # in this request — including 401/403 rejections that never reach
        # a route handler at all. Observation only: nothing below changes
        # the request, the response, or the existing log output.
        set_request_meta(
            ip_address=_resolve_client_ip(request),
            user_agent=(request.headers.get("user-agent") or "")[:_MAX_USER_AGENT_LEN] or None,
            client_session_id=request.headers.get(CLIENT_SESSION_ID_HEADER),
            http_method=request.method,
            http_path=request.url.path
        )

        start = time.perf_counter()

        logger.info(
            f"Request started: {request.method} {request.url.path}",
            extra={
                "event": "request_started",
                "method": request.method,
                "path": request.url.path,
                "query_params": dict(request.query_params),
                "client": request.client.host if request.client else "-"
            }
        )

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.exception(
                f"Unhandled exception on {request.method} {request.url.path} "
                f"after {duration_ms}ms",
                extra={
                    "event": "request_failed",
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": duration_ms
                }
            )
            raise

        duration_ms = round((time.perf_counter() - start) * 1000, 2)

        logger.info(
            f"Request completed: {request.method} {request.url.path} "
            f"-> {response.status_code} in {duration_ms}ms",
            extra={
                "event": "request_completed",
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms
            }
        )

        response.headers[REQUEST_ID_HEADER] = request_id

        return response

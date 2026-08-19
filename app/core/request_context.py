"""
Per-request context, propagated via contextvars so any log statement
anywhere in the call stack (route -> service -> repository) can be
automatically tagged with the current request_id / user, without having
to thread those values through every function signature.

Set once per request by RequestLoggingMiddleware (app/core/middleware.py)
and by get_current_user() (app/api/dependencies/auth.py); read by the
logging filter in app/core/logging.py that injects these into every
LogRecord, and by AuditService / ActivityService.

──────────────────────────────────────────────────────────────────────
WHY THERE IS ONE ContextVar HOLDING A DICT, NOT ONE PER FIELD
──────────────────────────────────────────────────────────────────────
This module originally used one ContextVar per field with a plain
`.set()` on each. That silently did not work for anything set AFTER the
middleware, and the symptom was every log line showing `user=-` even on
authenticated requests, plus NULL user_id/user_email/role on audit rows
written from inside a route.

The cause is how FastAPI runs synchronous code. A dependency or route
handler declared with `def` (not `async def`) is executed in a worker
thread via `anyio.to_thread.run_sync`, which runs it inside a COPY of
the caller's context. Copies propagate downward only:

    async middleware task          <- .set() here IS visible below
      └── copy -> thread A: get_current_user()   (def)
      │            .set() here is LOST when the thread returns
      └── copy -> thread B: chat() route         (def)
                   sees the middleware's values, never thread A's

So request_id (set in the async middleware) worked, while user_email /
customer_id / user_id / role (set in the sync get_current_user
dependency) were discarded before the route ever ran.

Storing ONE dict in ONE ContextVar fixes this without changing the
concurrency model. Every context copy holds a reference to the SAME dict
object, so mutating the dict in thread A is immediately visible in
thread B. Nothing else in the codebase has to change: every public
function below keeps the exact name and signature it had before.

Isolation is preserved because the middleware installs a FRESH dict at
the start of every request, and each request runs in its own task with
its own context.
"""
from contextvars import ContextVar
from typing import Any, Optional

# One mutable container per request. See the module docstring for why
# this is a single dict rather than one ContextVar per field.
_context_store: ContextVar[Optional[dict]] = ContextVar(
    "request_context_store",
    default=None
)

# Keys held in the store. Listed explicitly so init_request_context()
# always produces a fully-formed dict and no getter can KeyError.
_FIELDS = (
    "request_id",
    "user_email",
    "customer_id",
    "super_admin_id",
    "user_id",
    "user_role",
    "ip_address",
    "user_agent",
    "session_id",
    "client_session_id",
    "http_method",
    "http_path"
)


def init_request_context() -> dict:
    """
    Installs a fresh, empty context for this request. Called once by
    RequestLoggingMiddleware before anything else, from the async
    request task — which is the ancestor context of every threadpool
    dependency and route handler that follows, so they all share this
    exact dict object.

    Also guarantees a clean slate per request: no value can leak from a
    previous request that happened to reuse the same worker thread.
    """
    store = {field: None for field in _FIELDS}
    _context_store.set(store)
    return store


def _store() -> dict:
    """
    The current request's context dict.

    Outside a request (a management script, a background job, a unit
    test that calls a service directly) no middleware has run, so one is
    created lazily. In that situation the lazily-created store lives
    only in the calling context — which is correct, since there is no
    request to scope it to.
    """
    store = _context_store.get()

    if store is None:
        store = init_request_context()

    return store


def _get(key: str) -> Any:
    store = _context_store.get()

    # Read path deliberately does NOT create a store — a getter should
    # never have a side effect, and "no request" simply means None.
    return store.get(key) if store else None


def set_request_id(request_id: str) -> None:
    _store()["request_id"] = request_id


def get_request_id() -> Optional[str]:
    return _get("request_id")


def set_current_user(
    email: Optional[str],
    customer_id: Optional[int] = None,
    super_admin_id: Optional[int] = None,
    user_id: Optional[int] = None,
    role: Optional[str] = None
) -> None:
    """
    Called from get_current_user() once a request's JWT has been
    resolved to a real user, so every log line for the rest of this
    request (RAG retrieval, cache hits, LLM calls, DB queries) can be
    traced back to who triggered it — without ever logging the token
    itself or the password.

    super_admin_id: the Super Admin who owns this request's tenant —
    i.e. Customer.created_by for a tenant user (CUSTOMER/ADMIN/USER), or
    the user's own id when the caller IS the Super Admin. None only when
    it genuinely can't be resolved (e.g. a legacy Customer row with no
    created_by recorded).

    user_id / role: added for the Audit Framework, both keyword-optional
    and defaulting to None so the original three-argument call signature
    still works unchanged for any existing caller.

    NOTE: this runs inside a threadpool worker (get_current_user is a
    sync `def` dependency). It mutates the shared dict rather than
    calling ContextVar.set() precisely so the values survive the return
    from that worker thread — see the module docstring.
    """
    store = _store()
    store["user_email"] = email
    store["customer_id"] = customer_id
    store["super_admin_id"] = super_admin_id
    store["user_id"] = user_id
    store["user_role"] = role


def get_current_user_email() -> Optional[str]:
    return _get("user_email")


def get_current_customer_id() -> Optional[int]:
    return _get("customer_id")


def get_current_super_admin_id() -> Optional[int]:
    return _get("super_admin_id")


# ─────────────────────────────────────────────────────────────────────
# Audit Framework additions.
#
# Network/browser attribution, captured by the middleware before the
# request is routed — so even a request rejected at authentication
# (401/403) still has a full fingerprint available for the security
# audit record.
# ─────────────────────────────────────────────────────────────────────

def set_request_meta(
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    client_session_id: Optional[str] = None,
    http_method: Optional[str] = None,
    http_path: Optional[str] = None
) -> None:
    store = _store()
    store["ip_address"] = ip_address
    store["user_agent"] = user_agent
    store["http_method"] = http_method
    store["http_path"] = http_path

    # The browser tab id from X-Session-ID. UNTRUSTED and quarantined:
    # it is only ever surfaced as activity metadata.
    store["client_session_id"] = client_session_id

    # `session_id` is deliberately NOT seeded from the client value.
    #
    # It stays None until set_session_id() installs the JWT's `jti` in
    # get_current_user(). That means a pre-authentication audit row
    # (failed login, invalid token) has session_id = NULL — which is the
    # correct and honest record: no server-issued session existed yet.
    # Those rows are still correlated by request_id and ip_address.
    #
    # Seeding a client value here would let an attacker choose the
    # session id their own audit rows are filed under, and pre-seed one
    # a victim's rows would later join to — classic session fixation.
    store["session_id"] = None


def get_current_user_id() -> Optional[int]:
    return _get("user_id")


def get_current_user_role() -> Optional[str]:
    return _get("user_role")


def get_ip_address() -> Optional[str]:
    return _get("ip_address")


def get_user_agent() -> Optional[str]:
    return _get("user_agent")


def set_session_id(session_id: Optional[str]) -> None:
    """
    Installs the AUTHORITATIVE session id for this request — the `jti`
    claim lifted from the verified JWT by get_current_user().

    Called after set_request_meta(), so it deliberately overwrites the
    provisional client-supplied value. From this point on every audit
    row written during the request carries a session id the server
    itself issued at login.

    Passing None is a no-op rather than a clear: a token that predates
    this feature has no `jti`, and wiping the provisional value would
    lose correlation for no benefit.
    """
    if session_id:
        _store()["session_id"] = session_id


def get_session_id() -> Optional[str]:
    return _get("session_id")


def get_client_session_id() -> Optional[str]:
    """
    The raw, untrusted X-Session-ID header. Exposed only so the frontend
    activity tracker can group a browser tab's events; never used for
    anything security-relevant.
    """
    return _get("client_session_id")


def get_http_method() -> Optional[str]:
    return _get("http_method")


def get_http_path() -> Optional[str]:
    return _get("http_path")


def get_audit_context() -> dict:
    """
    One-shot snapshot of everything AuditService / ActivityService need
    to stamp onto a record. Returned as a NEW dict (not the live store)
    so a caller can never mutate the request context by accident.

    Outside a request every value is simply None — the audit row is
    still written, just without actor/network attribution.
    """
    store = _context_store.get() or {}

    return {
        "user_id": store.get("user_id"),
        "user_email": store.get("user_email"),
        "role": store.get("user_role"),
        "customer_id": store.get("customer_id"),
        "request_id": store.get("request_id"),
        "ip_address": store.get("ip_address"),
        "user_agent": store.get("user_agent"),
        # Server-issued (JWT jti) — the only value anything
        # security-relevant may use.
        "session_id": store.get("session_id"),
        # Untrusted browser tab id. Consumed solely by ActivityService,
        # and only as metadata.
        "client_session_id": store.get("client_session_id"),
        "http_method": store.get("http_method"),
        "http_path": store.get("http_path")
    }

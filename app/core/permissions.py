"""
Role -> permission mapping, for the JWT `permissions` claim.

WHY THIS IS A MAP AND NOT A NEW TABLE
─────────────────────────────────────
This deployment's RBAC is already fully expressed, and enforced, by the
role gates in app/api/dependencies/auth.py:

    get_super_admin    -> role must be SUPER_ADMIN
    get_company_admin  -> role must be CUSTOMER (with a customer_id)
    get_customer_user  -> any tenant-scoped account
    get_current_user   -> any authenticated account

Those gates are the source of truth and are NOT being replaced — the
brief says RBAC must not be removed. This module derives a *declarative
description* of what each role can do, so the token can carry it.

Introducing a `permissions` / `role_permissions` table instead would
mean two sources of truth for the same rules, a per-request join to read
them, and a migration path for existing roles — all to express something
that is currently static. When permissions become genuinely
per-user/per-tenant configurable, this map is the seam to replace with a
repository; nothing else has to change, because everything reads through
permissions_for_role().

RELATIONSHIP TO ENFORCEMENT
───────────────────────────
The claim is descriptive, not (yet) authoritative:

  - Existing routes keep using the existing role dependencies. Not one
    route signature changes.
  - require_permission() below is provided for NEW routes that want
    permission-based gating, and as the migration path off role checks.

Keeping enforcement on the server's own re-derivation (never on the
claim as presented by the client) is the OWASP ASVS V4 requirement:
authorization decisions must not be made from client-held data. A
tampered token fails signature verification, so the claim cannot be
forged — but the server still re-derives from `role` rather than
trusting the array, so a token minted before a permission change can
never grant more than the current map allows.
"""
from typing import FrozenSet

from fastapi import Depends, HTTPException

from app.models.user import Role


class Permission:
    """
    Every distinct capability the API exposes today, named
    `<resource>:<action>`. Each one corresponds to a real, existing
    route — nothing aspirational is listed, because a permission the
    server does not enforce is worse than no permission at all.
    """

    # Tenant (customer) administration — Super Admin only.
    CUSTOMER_CREATE = "customer:create"
    CUSTOMER_READ = "customer:read"
    CUSTOMER_UPDATE = "customer:update"
    CUSTOMER_DELETE = "customer:delete"

    # User administration.
    USER_CREATE = "user:create"
    USER_READ = "user:read"
    USER_UPDATE = "user:update"
    USER_DELETE = "user:delete"

    # Documents / knowledge base.
    DOCUMENT_CREATE = "document:create"
    DOCUMENT_READ = "document:read"
    DOCUMENT_DELETE = "document:delete"
    CACHE_CLEAR = "cache:clear"

    # Chat / RAG.
    CHAT_USE = "chat:use"
    CONVERSATION_READ = "conversation:read"
    CONVERSATION_DELETE = "conversation:delete"

    # Audit console.
    AUDIT_READ = "audit:read"
    ACTIVITY_READ = "activity:read"
    SESSION_READ = "session:read"
    SESSION_REVOKE = "session:revoke"


# ─────────────────────────────────────────────────────────────────────
# The map. Each entry is derived directly from which dependency guards
# the corresponding routes today — see the module docstring.
# ─────────────────────────────────────────────────────────────────────
_ROLE_PERMISSIONS = {

    # Manages tenants and their users/documents. Deliberately has NO
    # chat permission: the spec is explicit that a Super Admin must
    # never access a Customer's or User's chats, and get_customer_user
    # enforces that today by requiring a customer_id (which a Super
    # Admin does not have).
    Role.SUPER_ADMIN: frozenset({
        Permission.CUSTOMER_CREATE,
        Permission.CUSTOMER_READ,
        Permission.CUSTOMER_UPDATE,
        Permission.CUSTOMER_DELETE,
        Permission.USER_CREATE,
        Permission.USER_READ,
        Permission.USER_UPDATE,
        Permission.USER_DELETE,
        Permission.DOCUMENT_CREATE,
        Permission.DOCUMENT_READ,
        Permission.DOCUMENT_DELETE,
        Permission.AUDIT_READ,
        Permission.ACTIVITY_READ,
        Permission.SESSION_READ,
        Permission.SESSION_REVOKE,
    }),

    # The company admin: one per tenant, auto-created with the Customer.
    # Manages that company's own documents and users, and can chat.
    Role.CUSTOMER: frozenset({
        Permission.USER_CREATE,
        Permission.USER_READ,
        Permission.USER_UPDATE,
        Permission.USER_DELETE,
        Permission.DOCUMENT_CREATE,
        Permission.DOCUMENT_READ,
        Permission.DOCUMENT_DELETE,
        Permission.CACHE_CLEAR,
        Permission.CHAT_USE,
        Permission.CONVERSATION_READ,
        Permission.CONVERSATION_DELETE,
    }),

    # Regular staff account: chat-only, by spec. Cannot upload or delete
    # documents, cannot manage users.
    Role.USER: frozenset({
        Permission.CHAT_USE,
        Permission.CONVERSATION_READ,
        Permission.CONVERSATION_DELETE,
    }),

    # A global role that exists in the schema (see app/schemas/auth.py)
    # but currently guards no routes of its own. Given read-only
    # visibility rather than an empty set, so it is useful without
    # silently granting anything a route does not already allow.
    Role.ADMIN: frozenset({
        Permission.CUSTOMER_READ,
        Permission.USER_READ,
        Permission.DOCUMENT_READ,
        Permission.AUDIT_READ,
        Permission.ACTIVITY_READ,
        Permission.SESSION_READ,
    }),
}


def permissions_for_role(role: str) -> FrozenSet[str]:
    """
    Single accessor for the map. An unknown role yields an empty set
    rather than raising — a token is not the place to discover a config
    problem, and the role gates would reject such a caller anyway.
    """
    return _ROLE_PERMISSIONS.get(role, frozenset())


def permissions_claim(role: str) -> list:
    """
    The value that goes into the JWT's `permissions` claim.

    Sorted so the claim is deterministic: two tokens issued for the same
    role are byte-identical in this field, which makes tokens
    diff-able in logs and keeps test assertions stable.
    """
    return sorted(permissions_for_role(role))


def has_permission(role: str, permission: str) -> bool:
    return permission in permissions_for_role(role)


def require_permission(permission: str):
    """
    Permission-based route guard, for NEW routes and as the migration
    path off role checks. No existing route uses it — adding it to one
    would change that route's behaviour, which the brief forbids.

    Usage:
        @router.get("/thing", dependencies=[Depends(require_permission(
            Permission.DOCUMENT_READ))])

    SECURITY: re-derives the permission set from the authenticated
    user's CURRENT role, never from the `permissions` array in the
    presented token. The claim is signed and so cannot be forged, but it
    is still a point-in-time snapshot — a token issued before a role
    change would otherwise keep working against the old rules. OWASP
    ASVS V4: authorization decisions are made server-side, from
    server-held state.
    """
    # Imported here rather than at module scope: app.api.dependencies.auth
    # imports from app.models and app.services, and a top-level import
    # would create a cycle with any of those importing this module.
    from app.api.dependencies.auth import get_current_user

    def _guard(current_user=Depends(get_current_user)):
        if not has_permission(current_user.role, permission):
            # Deliberately mirrors the message style of the existing
            # role gates so clients see a consistent 403 shape.
            raise HTTPException(
                status_code=403,
                detail=f"Access denied. Missing permission: {permission}"
            )
        return current_user

    return _guard

# Audit & Activity Framework — Architecture Review + Integration Plan

**Status:** ✅ IMPLEMENTED. See §9 for the as-built record and verification results.
**Repo reviewed:** `backend/` (FastAPI + SQLAlchemy 2.0 + Alembic + PostgreSQL/pgvector), `frontend/` (React 18 + Vite + axios + react-router 6)

---

## 1. Architecture Review (what actually exists today)

### 1.1 Layering
The real call chain is:

```
app/main.py  (FastAPI app, CORS, RequestLoggingMiddleware, exception handlers)
  -> app/api/router.py            (aggregates 7 v1 routers, no /api/v1 prefix — routes are /auth, /chat, ...)
    -> app/api/v1/*.py            (routes; construct repos + services inline)
      -> app/services/*.py        (business logic)
        -> app/repositories/*.py  (SQLAlchemy queries; own commit())
          -> app/models/*.py      (declarative Base from app/database/database.py)
```

**Important:** there is **no DI container**. Dependency injection is literally
`db: Session = Depends(get_db)` in the route, then `repo = XRepository(db)` and
`service = XService(repo)` constructed by hand. Services receive *repositories*,
not the `Session` (except `SemanticCacheService(db)` and `DocumentService`, which
reaches `self.repository.db`). **The audit framework must follow this same
hand-wired pattern** — introducing a DI container would violate "do not refactor".

### 1.2 Request Context (`app/core/request_context.py`)
Four `ContextVar`s exist today:

| var | set where | value |
|---|---|---|
| `_request_id` | `RequestLoggingMiddleware.dispatch()` | `X-Request-ID` header or generated uuid4 |
| `_user_email` | `get_current_user()` after JWT decode | `user.email` |
| `_customer_id` | `get_current_user()` | `user.customer_id` |
| `_super_admin_id` | `get_current_user()` | resolved via `Customer.created_by` |

**Gap for the audit spec:** `user_id`, `role`, `ip_address`, `user_agent`,
`session_id`, `method`, `path` are **not** in context. These must be added
**additively** (new ContextVars + new setters), never by changing the existing
`set_current_user()` signature — `app/core/logging.py::RequestContextFilter`
and `app/api/dependencies/auth.py` both depend on the current shape.

### 1.3 Middleware (`app/core/middleware.py`)
`RequestLoggingMiddleware` (BaseHTTPMiddleware) already:
- generates/propagates `request_id`,
- logs `request_started` / `request_completed` with `status_code` + `duration_ms`,
- catches + logs unhandled exceptions and **re-raises unchanged**,
- echoes `X-Request-ID` back on the response.

It already has `request.client.host` and `request.headers` in scope — it is the
**correct and only** place to capture IP / User-Agent / session id. No new
middleware class is needed for that; a small additive block inside `dispatch()`
is enough. A *separate* thin middleware is needed only for auto-auditing
401/403 responses (security events) so route code stays untouched.

### 1.4 Logging (`app/core/logging.py`)
Single logger `ai_document_search`, console + `RotatingFileHandler`, `text` or
`json` formatter, `RequestContextFilter` injects request context onto every
record, arbitrary `extra={...}` fields are folded into JSON output.
There is also a second, purpose-built human-readable sink:
`app/core/chat_summary.py` -> `logs/chat_summary.log` (one box per chat request).
**Reuse `logger` as-is. Audit failures go here (`logger.error`) and nowhere else.**

### 1.5 Authentication
`HTTPBearer` + `python-jose`. `get_current_user()` decodes JWT (`sub`=email),
loads the `User`, rejects inactive accounts, resolves `super_admin_id`, and
calls `set_current_user(...)`. Role gates: `get_super_admin`, `get_customer_user`,
`get_company_admin` — each raises `HTTPException(403)` after a `logger.warning`.
`AuthService.login()` raises `InvalidCredentialsException` (mapped to 401 in
`main.py`). **There is no logout endpoint and no `/auth/password-reset`** — the
frontend logout is purely client-side (`AuthContext.logout()` clears
localStorage). So "Logout" must be audited via a **new, additive, optional**
endpoint that the frontend may call; the existing client-side logout keeps
working untouched if it doesn't.

### 1.6 Database & models
`Base = declarative_base()` in `app/database/database.py`. Every model imports
that `Base`. **Two registration points must both be updated** or Alembic
autogenerate misbehaves:
- `app/models/__init__.py` (imports all 9 models)
- `app/database/base.py` (what `alembic/env.py` imports as `target_metadata`)

Migration chain head today: **`b2c3d4e5f6a7`** (`create_exact_cache_table`).
Full chain: `e473d23b5fa3 -> 21f89e8613d1 -> d0ac3aad39c4 -> ef44cfe1b32e ->
ff69ac9ff84c -> ca36f37bdc31 -> da04cf253e93 -> 85fbde5d9615 -> b7c9f1a2d3e4 ->
a1b2c3d4e5f6 -> b2c3d4e5f6a7`.

### 1.7 RAG / Cache flow (`app/services/rag_service.py`)
`RAGService.ask()` already produces exactly the telemetry the AI audit category
asks for, in a `diagnostics` dict: `exact_cache_status`, `semantic_cache_status`,
`vector_chunks`, `keyword_chunks`, `merged_chunks`, `embedding_model`,
`llm_model`, `embedding_time_sec`, `llm_time_sec`, `total_time_sec`,
`exact_cache_written`, `semantic_cache_written`, plus `token_usage`.
`app/api/v1/chat.py` **pops** `diagnostics` off the result before returning, so the
response shape stays frozen, and feeds it to `log_chat_summary(...)`.

**This is the single best integration point in the whole repo:** the AI/RAG audit
record can be written right beside the existing `log_chat_summary(...)` call from
the same already-collected values. Zero new plumbing, zero change to `RAGService`,
zero change to `ChatResponse`.

### 1.8 Frontend
Vite + React 18, `axios` instance in `src/api/axios.js` with a request
interceptor (attaches Bearer token) and a response interceptor (global 401 ->
redirect to `/login`). Routing in `App.jsx`: `/login`, `/dashboard` (USER),
`/company` (CUSTOMER), `/admin` (SUPER_ADMIN) — **panels, not routes**, for
Documents/Users/Chat (`DocumentsPanel`, `UsersPanel`, `ChatPanel` inside
`CompanyDashboard`). So "page open" tracking is really **panel-switch tracking**,
driven from the dashboard components, not from router events alone.

---

## 2. Design decisions (and why)

| Decision | Rationale |
|---|---|
| Two **physically separate** tables, models, repos, services (`audit_logs` / `activity_logs`) | Spec requires no mixing. Different retention, different consumers (SIEM vs product analytics). |
| Audit writes use a **dedicated short-lived `SessionLocal()`**, never the request's `db` | If audit reused the business `Session`, a failed audit flush would poison the business transaction and a business rollback would silently erase audit rows. This is the only way to honour "never roll back business tx because audit failed". |
| `AuditService` pulls actor/IP/UA/request_id from **contextvars**, not parameters | Spec: "No controller should manually pass these values." |
| Every `AuditService.log(...)` body wrapped in `try/except Exception -> logger.error(..., exc_info=True)` | Spec: audit must never break business flow. |
| `details` column = `JSONB` (not `JSON`) | GIN-indexable, needed later for "advanced filters" without redesign. |
| `timestamp` = `DateTime(timezone=True)`, UTC | Required for Splunk/ELK/Sentinel ingestion and date-range filters. |
| Keep `customer_id` column on both tables (nullable) | Reuses existing request context, costs nothing, keeps rows joinable. **No tenant-isolation logic is implemented** — per your client-specific deployment model. |
| Category/action/status as **plain string constants class** (like `app/models/user.py::Role`) | Matches existing style exactly; avoids a DB enum and the migration pain that comes with it. |
| Frontend activity endpoints accept a **batch** array | Lets the frontend flush on `visibilitychange`/`beforeunload` without a request per event; also the future path for optional click tracking. |
| No changes to any existing schema in `app/schemas/` | Frontend compatibility is non-negotiable. |

---

## 3. NEW files to be created

### Module 1 — Backend Audit Framework
| File | Purpose |
|---|---|
| `app/models/audit_log.py` | `AuditLog` model + `AuditCategory`, `AuditAction`, `AuditStatus` constant classes (styled after `Role`). |
| `app/repositories/audit_repository.py` | `AuditRepository` — `create()`, `create_many()`, `search()` (filters + pagination), `count()`. Query-only + insert; **never called from business repos**. |
| `app/services/audit_service.py` | `AuditService` — context-aware `log()`, plus typed helpers `log_auth()`, `log_user_event()`, `log_document_event()`, `log_ai_event()`, `log_security_event()`, `log_system_event()`. Owns the isolated session + swallow-all-errors guarantee. |
| `app/schemas/audit.py` | Pydantic response/filter models for the **new** read endpoints only. |
| `app/api/v1/audit.py` | `GET /audit/logs` (filters + pagination), `GET /audit/logs/{id}`, `GET /audit/categories`, `GET /audit/export` (csv now; xlsx/pdf later). Guarded by `get_super_admin` / `get_company_admin`. |
| `app/core/audit_context.py` | *(optional, small)* helpers to read the extended context safely outside a request. |
| `alembic/versions/<rev>_create_audit_logs_table.py` | Hand-written migration, `down_revision = 'b2c3d4e5f6a7'`. |

### Module 2 — Frontend Activity Tracking
| File | Purpose |
|---|---|
| `app/models/activity_log.py` | `ActivityLog` model + `ActivityType` constants (`PAGE_VIEW`, `NAVIGATION`, `SESSION_START`, `SESSION_END`, `FEATURE_USAGE`, reserved `CLICK`). |
| `app/repositories/activity_repository.py` | `ActivityRepository` — `create()`, `create_many()`, `search()`, aggregate helpers for time-spent. |
| `app/services/activity_service.py` | `ActivityService` — validation, context enrichment, batch ingest. Same never-break-the-request guarantee. |
| `app/schemas/activity.py` | `ActivityEventIn`, `ActivityBatchIn`, `ActivityLogOut`. |
| `app/api/v1/activity.py` | `POST /activity/events` (batch), `POST /activity/session/start`, `POST /activity/session/end`, `GET /activity/logs` (admin read). |
| `alembic/versions/<rev>_create_activity_logs_table.py` | Chained after the audit migration. |

### Frontend (additive only)
| File | Purpose |
|---|---|
| `src/api/activityTracker.js` | Tiny queue + flush client (`trackPageView`, `trackFeature`, `startSession`, `endSession`); uses the existing `api` axios instance; fails silently. |
| `src/hooks/useActivityTracking.js` | Hook the dashboards call on panel switch / mount. |

---

## 4. EXISTING files to be MODIFIED (and exactly how)

| File | Change | Why | Risk |
|---|---|---|---|
| `app/core/request_context.py` | **Add** `_user_id`, `_user_role`, `_ip_address`, `_user_agent`, `_session_id`, `_http_method`, `_path` ContextVars + new getters/setters. Existing functions untouched; `set_current_user()` gains **keyword-only optional** `user_id=None, role=None` so existing positional calls still work. | Spec requires auto-capture of user/role/IP/browser/session. | **Very low** — purely additive; existing call sites unaffected. |
| `app/core/middleware.py` | **Add** ~6 lines in `dispatch()` to `set_request_meta(ip, user_agent, session_id, method, path)` before `call_next`. No change to logging or to what the client receives. | Only place with the raw `Request`. | **Very low** |
| `app/api/dependencies/auth.py` | Pass `user_id=user.id, role=user.role` into `set_current_user(...)`; add `AuditService` calls in the 401/403 branches (invalid token, no `sub`, user not found, deactivated) and in `get_super_admin`/`get_customer_user`/`get_company_admin` 403 branches. Exceptions still raised identically. | Spec's Auth + Security events. Cannot be done from routes. | **Low** — audit calls are wrapped and cannot raise. |
| `app/services/auth_service.py` | Audit `LOGIN_SUCCESS` after token creation; audit `LOGIN_FAILED` before each `raise InvalidCredentialsException()`. Return values unchanged. | Spec: Login / Failed Login. Business service is the correct owner per your rules. | **Low** |
| `app/api/v1/auth.py` | Audit `USER_CREATED` on `/auth/register`. **Add** new `POST /auth/logout` (returns `{"message": ...}`) — additive route, existing routes untouched. | Spec: Logout. Frontend can adopt it later; nothing breaks if it doesn't. | **Low** |
| `app/api/v1/company_users.py` | Route currently contains the business logic (no service layer for company users). To honour "services call AuditService, routes don't", introduce **`app/services/company_user_service.py`** wrapping the existing logic **verbatim**, and have the routes delegate. Same responses, same status codes, same exceptions. | Spec rule: routes must not write audit logs. | **Medium** — this is the only place with real code movement. *If you prefer zero movement, say so and I'll call `AuditService` from these 4 routes directly instead.* |
| `app/services/customer_service.py` | Audit customer create / update / soft-delete / role-cascade events. | User Management + System categories. | **Low** |
| `app/services/document_service.py` | Audit `DOCUMENT_UPLOADED`, `DOCUMENT_PARSED`, `CHUNKS_CREATED`, `EMBEDDINGS_CREATED`, `DOCUMENT_DELETED`, `INDEX_FAILED`. Chunk/embedding counts read from the existing indexing return values. | Document + Knowledge Base categories. | **Low** |
| `app/api/v1/chat.py` | Beside the existing `log_chat_summary(...)`, add one `audit_service.log_ai_event(...)` from the **same already-popped `diagnostics` dict**. `result` returned unchanged. | AI/RAG category with zero new instrumentation. **Question text is NOT stored** — only `question_length`, `question_hash`, models, chunk counts, cache status, tokens, timings, conversation_id. | **Low** |
| `app/api/router.py` | `include_router(audit_router)`, `include_router(activity_router)`. | Register the 2 new routers. | **None** |
| `app/models/__init__.py` | Import `AuditLog`, `ActivityLog`. | Model registration. | **None** |
| `app/database/base.py` | Import `AuditLog`, `ActivityLog`. | Required for Alembic `target_metadata`. | **None** |
| `app/core/config.py` | Add defaults: `AUDIT_ENABLED=True`, `ACTIVITY_TRACKING_ENABLED=True`, `AUDIT_RETENTION_DAYS=365`, `ACTIVITY_RETENTION_DAYS=90`, `AUDIT_CLICK_TRACKING_ENABLED=False`. All have defaults -> **`.env` needs no change**. | Kill switches + future click tracking. | **None** |
| `frontend/src/context/AuthContext.jsx` | On `login()` -> `startSession()`; on `logout()` -> `endSession()` + optional `POST /auth/logout`. Wrapped in try/catch. | Session start/end. | **Low** |
| `frontend/src/pages/CompanyDashboard.jsx`, `Dashboard.jsx`, `SuperAdminDashboard.jsx` | One `useActivityTracking(activePanel)` call each. | Page/panel view + time-spent. | **Low** |

**Not touched at all:** every file under `app/schemas/` (existing), `app/repositories/*` (existing), `app/services/rag_service.py`, `embedding_service.py`, `llm_service.py`, `cache_service.py`, `semantic_cache_service.py`, `indexing_service.py`, `chunking_service.py`, `file_parser_service.py`, `app/core/logging.py`, `app/core/security.py`, `app/core/chat_summary.py`, `app/main.py`, all existing models, all existing migrations.

---

## 5. Table designs

### `audit_logs`
```
id              BIGSERIAL PK
timestamp       TIMESTAMPTZ NOT NULL   -- event time (UTC)
user_id         INTEGER NULL FK users.id ON DELETE SET NULL
user_email      VARCHAR(320) NULL      -- denormalized, survives user deletion
role            VARCHAR(32) NULL
customer_id     INTEGER NULL FK customers.id ON DELETE SET NULL
category        VARCHAR(64) NOT NULL   -- AUTHENTICATION | USER_MANAGEMENT | DOCUMENT_MANAGEMENT
                                       -- | KNOWLEDGE_BASE | AI_RAG | SECURITY | SYSTEM
                                       -- | CONFIGURATION | BACKUP
action          VARCHAR(128) NOT NULL  -- LOGIN_SUCCESS, DOCUMENT_UPLOADED, ...
resource        VARCHAR(128) NULL      -- "document", "user", "conversation"
resource_id     VARCHAR(128) NULL      -- string, so UUIDs/filenames also fit
status          VARCHAR(32) NOT NULL   -- SUCCESS | FAILURE | DENIED
details         JSONB NULL             -- metadata only, never prompts/document text
request_id      VARCHAR(64) NULL
ip_address      VARCHAR(64) NULL       -- 64 chars covers IPv6 + proxy chains
user_agent      VARCHAR(512) NULL
session_id      VARCHAR(128) NULL
http_method     VARCHAR(10) NULL
http_path       VARCHAR(512) NULL
http_status     INTEGER NULL
duration_ms     INTEGER NULL
created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
```
Indexes: `timestamp DESC`; `(category, timestamp DESC)`; `(user_id, timestamp DESC)`;
`(action, timestamp DESC)`; `(status, timestamp DESC)`; `request_id`;
`(resource, resource_id)`; GIN on `details`.

### `activity_logs`
```
id              BIGSERIAL PK
timestamp       TIMESTAMPTZ NOT NULL
user_id         INTEGER NULL FK users.id ON DELETE SET NULL
user_email      VARCHAR(320) NULL
role            VARCHAR(32) NULL
customer_id     INTEGER NULL FK customers.id ON DELETE SET NULL
session_id      VARCHAR(128) NULL
activity_type   VARCHAR(64) NOT NULL   -- PAGE_VIEW | NAVIGATION | SESSION_START
                                       -- | SESSION_END | FEATURE_USAGE | CLICK (reserved)
page            VARCHAR(128) NULL      -- DASHBOARD | CHAT | DOCUMENTS | KNOWLEDGE_BASE
                                       -- | SETTINGS | REPORTS | USERS
feature         VARCHAR(128) NULL
from_page       VARCHAR(128) NULL      -- navigation source
duration_ms     INTEGER NULL           -- time spent on page
metadata_json   JSONB NULL
client_timestamp TIMESTAMPTZ NULL      -- browser clock, kept separate from server clock
request_id      VARCHAR(64) NULL
ip_address      VARCHAR(64) NULL
user_agent      VARCHAR(512) NULL
created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
```
Indexes: `timestamp DESC`; `(user_id, timestamp DESC)`; `(session_id, timestamp)`;
`(activity_type, timestamp DESC)`; `(page, timestamp DESC)`; GIN on `metadata_json`.

**Why these are future-ready without a redesign:** flat, denormalized, string-typed
dimensions + a JSONB bag = a textbook SIEM/BI event table. CSV/Excel/PDF export is
a serializer over `search()`. Splunk/ELK/Sentinel ingest either tails the JSON log
or reads `timestamp > last_seen` off the indexed column. Power BI/Grafana point
straight at the table. Pagination/date-range/filters are already index-backed.

---

## 6. Sensitive-data policy (enforced in code)
Never written to `audit_logs.details`: passwords, hashes, JWTs, question text,
LLM answers, chunk text, `extracted_text`, file contents.
Written instead: lengths, SHA-256 hashes, counts, model names, ids, timings, status.

---

## 7. Verification plan
1. `alembic upgrade head` on a copy of the DB; confirm only the two new tables appear (`\dt`, `\d+ audit_logs`).
2. `alembic downgrade -2` then `upgrade head` — clean round-trip.
3. `pytest tests/` — existing `tests/test_auth.py` must pass unchanged.
4. Snapshot `GET /openapi.json` before/after; diff must show **only additions** (new `/audit/*`, `/activity/*`, `/auth/logout` paths + new schemas), zero modifications to existing paths/schemas.
5. Manual smoke: login -> list documents -> upload -> chat -> delete -> logout; compare each response body byte-for-byte against pre-change output.
6. Failure-injection: point `AuditService` at a bad table name and re-run the smoke test — every business call must still return 200 and the errors must appear only in `logs/app.log`.
7. Frontend: `npm run build` clean; full click-through of all three dashboards with the backend's activity endpoint stopped — UI must behave identically.

---

## 8. Open questions — ANSWERED
1. **`company_users.py`** → **No refactor.** Existing logic, signatures, responses and status codes untouched; `AuditService` is called directly from the 4 route handlers. Documented in-file as a deliberate exception to the "services only" rule.
2. **`/auth/logout`** → **Added, audit-only.** JWT stays stateless — no blacklist, no server-side session, no change to the auth flow. The endpoint records the event and returns `{"message": "Logged out successfully"}`. Frontend calls it fire-and-forget; the existing client-side logout is unchanged.
3. **Audit read API access** → **`get_super_admin` only**, applied at router level so no future route can ship without the gate. Company Admins and Users have no access.

---

## 9. As-built record

### 9.1 Deviations from the plan
| Planned | As built | Why |
|---|---|---|
| Extract `CompanyUserService` | Not extracted; direct `AuditService` calls in 4 handlers | Your answer #1 |
| `app/core/audit_context.py` helper module | Not created; `get_audit_context()` added to the existing `request_context.py` instead | One fewer file, and the context already lived there |
| CSV export only mentioned | Implemented and **streamed** via `iter_for_export()` + `yield_per` | Exporting a year of history must not load the table into RAM |
| — | Added `_SAFE_KEY_ALLOWLIST` in `audit_service.py` | The `question` blocklist fragment was silently redacting `question_length` / `question_sha256`, the two fields that make the AI trail useful **without** storing the question |
| — | Added `use_context_actor=False` for AUTHENTICATION events | Without it, a failed login from a browser already holding someone else's valid token recorded the attempted email next to the *token holder's* `user_id`/`role` — an actively misleading row |
| Frontend `logout` becomes async | Kept **synchronous**; both calls fired unawaited before the token is cleared | An async logout would make the user wait on two network calls, and hang if the backend were unreachable. Axios' request interceptor reads the token synchronously on `.post()`, so the requests are already in flight with a valid header by the time the token is removed |

### 9.2 Final migration chain
`... -> b2c3d4e5f6a7 -> c1d2e3f4a5b6 (audit_logs) -> d2e3f4a5b6c7 (activity_logs)` — single head, verified with `alembic heads`.

### 9.3 Verification performed
| # | Check | Result |
|---|---|---|
| 1 | `python -m py_compile` on all 26 new/modified backend files | PASS |
| 2 | `pyflakes` on all new/modified backend files | Clean (0 warnings) |
| 3 | `from app.main import app` — full application import | PASS, 38 routes |
| 4 | **OpenAPI diff**: all 32 pre-existing path+method combinations re-checked for status codes, request-body schema `$ref`, response-model `$ref` and parameter lists | **Zero changes.** Only additions: 7 `/activity/*` + 5 `/audit/*` + `/auth/logout` paths, and 11 new component schemas. No existing schema modified |
| 5 | `pytest tests/` | 1 passed |
| 6 | `alembic upgrade b2c3d4e5f6a7:head --sql` (offline DDL render) | Emits only `CREATE TABLE`/`CREATE INDEX` for the two new tables — no `ALTER`/`DROP` against anything existing |
| 7 | `alembic heads` | Single head, no branch |
| 8 | **Failure injection** — `SessionLocal` replaced with a class that raises on construct, then with one that raises on `commit()` | No exception escaped any `AuditService`/`ActivityService` method; errors appeared only in `logs/app.log`; `log()` returned `None` in every case |
| 9 | **Sanitizer** — payload containing `password`, `hashed_password`, `access_token`, `api_key`, `question`, `answer`, `chunk_text`, `extracted_text` and a nested `secret` | All redacted, including nested. `question_length`, `question_sha256`, `total_tokens`, `filename` preserved |
| 10 | **Live write test** (SQLite) — real service → repository → ORM path with a simulated request context | Rows persisted with `user_id`/`role`/`ip`/`request_id`/`session_id` auto-captured from context; caller passed none of them |
| 11 | **Actor-bleed test** — failed login submitted while a valid token for another user is in context | `user_id`/`role` NULL, attempted email recorded, network context still captured; non-auth events still use the context actor |
| 12 | CLICK event submitted while `ACTIVITY_CLICK_TRACKING_ENABLED=False` | Rejected, not persisted |
| 13 | Frontend — all 8 new/modified JS/JSX files parsed with acorn + acorn-jsx | PASS |
| 14 | `get_audit_context()` called outside any request | All values `None`, no exception |

**Not verifiable in this environment:** `alembic upgrade head` against live PostgreSQL (no server available in the sandbox — the DDL was validated offline instead), and `npm run build` (`node_modules` contains Windows-native rollup/esbuild binaries; the source was parse-checked instead).

### 9.4 Post-deployment fix: NULL actor on AI_RAG rows

**Symptom.** `audit_logs` rows 6 and 7 (`AI_RAG / RESPONSE_GENERATED`) had `user_id`, `user_email`, `role` and `customer_id` all NULL, even though `/chat/` requires an authenticated user. `request_id` and `ip_address` on the same rows were populated correctly.

**Root cause — pre-existing, not introduced by the audit framework.** FastAPI runs a dependency or route handler declared with `def` (rather than `async def`) in a worker thread via `anyio.to_thread.run_sync`, which executes it inside a *copy* of the caller's context. Context copies propagate downward only:

```
async middleware task            <- ContextVar.set() here IS visible below
  ├── copy -> thread A: get_current_user()  (def)
  │            .set() here is DISCARDED when the thread returns
  └── copy -> thread B: chat() route        (def)
               sees the middleware's values, never thread A's
```

So `request_id` / `ip_address` (set in the async middleware) survived, while `user_email` / `customer_id` / `user_id` / `role` (set in the sync `get_current_user` dependency) were thrown away before the route ran. That is exactly the NULL/non-NULL split observed in the CSV.

**This also affected the project's existing logging.** Every line in `logs/app.log` emitted from inside a route showed `user=- | customer=- | super_admin=-` — the `RequestContextFilter` was reading the same discarded ContextVars. Confirmed in the shipped log file:

```
| req=75a2c2c5-... | user=- | customer=- | super_admin=- | ai_document_search:ask:279 | Que...
```

**Fix.** `app/core/request_context.py` now stores all fields in **one dict held in one ContextVar**, instead of one ContextVar per field. Every context copy holds a reference to the *same* dict object, so a mutation in thread A is visible in thread B. `RequestLoggingMiddleware` calls the new `init_request_context()` first, installing a fresh dict per request from the async task — the common ancestor context of every threadpool worker that follows.

Chosen over the alternatives because it changes nothing else: making `get_current_user` `async def` would have run its blocking SQLAlchemy queries on the event loop, and passing identity explicitly would have violated "no controller should manually pass these values" while leaving the logging bug unfixed.

Every public function in `request_context.py` kept its exact name and signature, so `app/core/logging.py`, `app/api/dependencies/auth.py` and `app/api/v1/chat.py` required no changes.

**Verified:**

| Check | Result |
|---|---|
| Minimal repro: sync dep `.set()` → sync route | Confirmed LOST before fix, WORKS after |
| Identity set in a sync dependency reaches a sync route | PASS (`user_id`, `user_email`, `role`, `customer_id`) |
| Middleware-set fields still present | PASS (`request_id`, `ip_address`, `user_agent`, `session_id`, `http_method`, `http_path`) |
| **12 concurrent requests** — cross-request identity leakage | PASS, zero leaks; 12 distinct `request_id`s |
| Existing logging repaired | `Request completed ... user=user2@x.com \| customer=2 \| super_admin=99` |
| Audit/activity failure-injection guarantees still hold | PASS |
| Sanitizer, `pytest`, full app import, pyflakes | PASS |

**Action required:** restart the backend. Rows 1–5 were correct already (auth events pass identity explicitly via overrides and never relied on context); rows 6–7 stay NULL as historical records. New `AI_RAG` rows will carry the full actor.

### 9.5 Rollback
- Disable without removing code: `AUDIT_ENABLED=False` and/or `ACTIVITY_TRACKING_ENABLED=False` in `.env`. Every call site becomes a no-op.
- Drop activity tracking only: `alembic downgrade c1d2e3f4a5b6`.
- Drop both tables: `alembic downgrade b2c3d4e5f6a7`.

---

## 11. Hardening pass — permissions claim, session fixation, force-logout coverage

Delivered after a gap audit of the implementation in §10 against the Hybrid JWT + Session specification. Only the genuine gaps were changed; everything already conforming was left alone.

### 11.1 Gap audit result
| Spec requirement | Status before | Action |
|---|---|---|
| JWT `sub`/`role`/`exp`/`iat`/`jti` (UUID v4) | conforming | none |
| `user_sessions` lifecycle, fail-open, throttle, revoke APIs, correlation | conforming | none |
| JWT **`permissions`** claim | **absent** | added |
| **Never trust `X-Session-ID`** | **violated** — header seeded `session_id` | fixed |
| Force logout on **Super Admin** user deactivate/delete | **absent** | added |
| Force logout on **tenant-wide** cascade | **absent** | added |
| `browser`/`platform`/`device_name`/`country`/`city`/`refresh_jti`/`updated_at` | absent | added |
| `login_at` / `ended_reason` naming | `started_at` / `end_reason` | renamed |

### 11.2 Session fixation — the real vulnerability found
`set_request_meta()` seeded `session_id` from the client's `X-Session-ID` header as a provisional value for pre-authentication requests. A client could therefore choose the session id its own audit rows were filed under, and pre-seed an id that a victim's rows would later join to.

Fixed by never letting a client value reach `session_id`. It is quarantined as `client_session_id` and surfaces only as `activity_logs.metadata_json.client_session_id`. Pre-authentication audit rows now carry `session_id = NULL` — the honest record, since no server-issued session existed yet; those rows remain correlated by `request_id` and `ip_address`.

`ActivityService` also lost its fallback to the client id: with no `jti`, `session_id` stays NULL rather than borrowing the browser tab id. A populated-but-wrong key would produce silent bogus joins against `user_sessions` and `audit_logs`.

### 11.3 Force-logout coverage — the security gap that mattered most
Deactivating a user through the **Super Admin** console, or deactivating an **entire tenant**, left every affected token live until expiry (up to 60 minutes). Only the company-admin path was covered.

Now covered, verified behaviourally against the real `CustomerService` control flow:

| Path | Mechanism |
|---|---|
| `company_users.py` deactivate / delete | `end_all_for_user` |
| `customer.py` Super Admin deactivate / delete | `end_all_for_user` |
| `CustomerService.update(is_active=False)` | `end_all_for_customer` — one bulk UPDATE, not N round-trips |
| `CustomerService.delete()` | routes through `update(is_active=False)`, same cascade |

### 11.4 Permissions claim
`app/core/permissions.py` maps each role to the capabilities its existing route gates already allow — derived from `get_super_admin` / `get_company_admin` / `get_customer_user`, not invented. No `permissions` table: that would create a second source of truth and a per-request join for something currently static. The map is the seam to replace with a repository when permissions become configurable.

The claim is **descriptive, not authoritative.** Existing routes keep their role dependencies (RBAC was not to be removed, and not one route signature changed). `require_permission()` is provided for new routes and re-derives from the user's *current* role rather than trusting the array — a token minted before a role change can never grant more than the current map allows (OWASP ASVS V4: authorization decided server-side).

Notably, `SUPER_ADMIN` has **no** `chat:use` permission, exactly matching `get_customer_user`, which rejects tenant-less accounts.

### 11.5 Deliberate deviation from the brief
> *"Never authenticate using database. Authentication must come only from JWT."*

`get_current_user()` still loads the `User` row. **Authentication** is already JWT-only — the signature check is what authenticates. That load exists for **authorization context** and, critically, the `is_active` check. Removing it would delete a security control: a deactivated account's token would keep working until expiry, and a role change would not take effect until re-login. The brief also forbids breaking existing behaviour and removing RBAC.

Net cost is one indexed lookup that already existed before this work. Flagged rather than silently resolved either way.

### 11.6 Verification
| Check | Result |
|---|---|
| JWT carries `sub`/`role`/`permissions`/`exp`/`iat`/`jti`; `jti` is UUID v4 | PASS |
| Permission map matches the real route gates (incl. Super Admin has no chat) | PASS |
| **`session_id` not seeded from header**; header quarantined | PASS |
| **Pre-auth audit row has NULL `session_id`** (no fixation) | PASS |
| Correlation `jwt.jti == user_sessions == audit_logs == activity_logs` | PASS |
| Activity ignores client id for `session_id`, preserves it in metadata | PASS |
| UA parsed (Chrome/Windows); Edge not mislabelled; API client not mislabelled | PASS |
| **`end_all_for_customer` closed all 5 tenant sessions**, reason + `revoked_by` recorded | PASS |
| `CustomerService.delete()` path ends sessions too | PASS |
| Revoked session rejected on next request | PASS |
| **Legacy token (no `jti`, no `permissions`, no `iat`) still decodes and is accepted** | PASS |
| `user_sessions` absent → still allows (fail-open) | PASS |
| DB unreachable → fail-open intact | PASS |
| Unknown role → empty permissions, no crash | PASS |
| Migration renders only `ALTER TABLE user_sessions` — nothing else touched | PASS |
| OpenAPI: 22 pre-existing paths / 33 operations unchanged | PASS |
| `TokenResponse` unchanged — `session_id`/`jti` never exposed | PASS |
| pytest, app import (44 routes), pyflakes, single alembic head | PASS |

### 11.7 Performance impact
| Change | Cost |
|---|---|
| `permissions` claim | JWT grows **+92 B (USER) / +252 B (CUSTOMER) / +337 B (SUPER_ADMIN)** per request header. No server-side cost — the map is an in-memory frozenset lookup |
| UA parsing | Once per **login**, not per request. Pure string matching, no dependency |
| Geo lookup | Zero — `resolve_geo()` is a documented no-op hook |
| `end_all_for_customer` | One bulk UPDATE instead of N per-user calls |
| Session revocation check | Unchanged: one indexed lookup per request; `last_seen_at` write throttled to 60 s |

Disable the per-request read entirely with `SESSION_REVOCATION_CHECK_ENABLED=False`.

### 11.8 Future recommendations (NOT implemented)
- **Refresh tokens** — `refresh_jti` column already exists; shorten the access token to ~15 min and pair it with a rotating refresh token. Shrinks the revocation window from 60 min to 15.
- **MFA** — a `mfa_verified` claim plus a `user_mfa` table; `user_sessions` is the natural place to record which factor authorised a session.
- **Device trust** — `browser`/`platform`/`device_name` are the foundation; add a `trusted_devices` table and flag logins from unrecognised devices.
- **Session anomaly detection** — `ip_address` is captured at login; alert when a session's requests arrive from a different IP or geography.
- **Concurrent session limits** — `end_all_for_user` already exists; cap live sessions per user at login.
- **Machine identity** — see §10.9. Still the largest structural gap for non-browser callers.

---

## 10. Session tracking (`user_sessions`)

### 10.1 The problem
`audit_logs.session_id` was NULL on every row. It was being read from a client-supplied `X-Session-ID` header that the frontend's axios instance never sent — and even if it had, a client-generated session id is forgeable, so it could never be relied on for an audit trail.

### 10.2 The enterprise pattern, and why
The session identifier in an audit record must be **server-issued and unforgeable**, because its whole purpose is non-repudiation: proving that a specific sequence of actions belonged to one specific login. The standard mechanism is the **`jti` claim** (RFC 7519, "JWT ID") — a server-generated id embedded in the *signed* token at login. It cannot be altered, replayed under another identity, or deliberately collided with. This is what OWASP ASVS V3 (Session Management) and NIST SP 800-53 AU-3 (Content of Audit Records) expect.

A pure `jti`-in-token design gives correlation, but cannot answer the two questions every enterprise deployment eventually gets asked:

1. *"Who is logged in right now?"* — requires server-side state.
2. *"Log that person out immediately."* — a stateless token is valid until `exp`; there is no way to retract it without a record to check against.

Hence `user_sessions`. The token stays stateless and its verification is unchanged; the session row is a lookup *alongside* verification, not a replacement for it.

### 10.3 One key joins all three tables
```
user_sessions.session_id  ==  audit_logs.session_id  ==  activity_logs.session_id  ==  JWT jti
```
So one login reconstructs end-to-end: when it started, from which IP, every audited action taken under it, every page viewed during it, and how it ended.

```sql
SELECT s.user_email, s.started_at, s.ended_at, s.end_reason, s.ip_address,
       (SELECT count(*) FROM audit_logs    a WHERE a.session_id = s.session_id) AS audit_events,
       (SELECT count(*) FROM activity_logs l WHERE l.session_id = s.session_id) AS page_events
FROM user_sessions s
ORDER BY s.started_at DESC;
```

The browser's own tab-scoped id is demoted to `activity_logs.metadata_json.client_session_id` — keeping it as the `session_id` would have left activity rows unable to line up with the other two tables.

### 10.4 Failure posture — the decision that matters most
`check_and_touch()` runs on the authentication path, so its failure mode was chosen deliberately:

- **FAILS OPEN.** Session tracking disabled, no `jti` in the token, no session row found, or the lookup itself errored → the request proceeds exactly as before. Fail-closed would mean one database hiccup signs out every user in the deployment — turning a monitoring problem into a total outage.
- **Only an explicitly read, non-NULL `ended_at` rejects a request.**
- `start()` / `end()` / `end_all_for_user()` never raise — a login must not fail because a session row could not be written.

Accepted trade-off, stated plainly: revocation is best-effort and bounded by the token's own `exp` (`ACCESS_TOKEN_EXPIRE_MINUTES`, currently 60). A revoked session is rejected as long as the DB is readable; worst case the token dies on its own within the hour. This is the standard posture for stateless-JWT-plus-revocation-list designs.

### 10.5 Performance
`last_seen_at` is written at most once per `SESSION_TOUCH_INTERVAL_SECONDS` (default 60), not per request — otherwise every read in the application becomes a write. The revocation lookup is a single hit on the UNIQUE index on `session_id`. "Who is online" uses a **partial index** (`WHERE ended_at IS NULL`) matching the query predicate exactly, so it never scans historical closed sessions.

Set `SESSION_REVOCATION_CHECK_ENABLED=False` to drop the per-request read entirely and return to pure stateless behaviour, while still recording sessions for reporting.

### 10.6 Files
**New:** `app/models/user_session.py`, `app/repositories/user_session_repository.py`, `app/services/session_service.py`, `app/schemas/session.py`, `app/api/v1/sessions.py`, `alembic/versions/e3f4a5b6c7d8_create_user_sessions_table.py`

**Modified:** `app/core/security.py` (adds `jti`/`iat` only if absent — signature unchanged), `app/core/request_context.py` (`set_session_id`, `client_session_id`), `app/api/dependencies/auth.py` (jti → context, revocation gate), `app/services/auth_service.py` (opens the session), `app/api/v1/auth.py` (logout closes it), `app/api/v1/company_users.py` (deactivate/delete ends live sessions), `app/services/activity_service.py` (uses jti), `app/models/audit_log.py` (+`SESSION_REVOKED`, `SESSION_EXPIRED`), `app/core/config.py`, `app/api/router.py`, `app/models/__init__.py`, `app/database/base.py`

### 10.7 New endpoints (Super Admin only, router-level gate)
| Endpoint | Purpose |
|---|---|
| `GET /audit/sessions/active` | who is logged in right now |
| `GET /audit/sessions` | full history, filterable |
| `GET /audit/sessions/{session_id}` | one session + its audit events |
| `POST /audit/sessions/{session_id}/revoke` | forced logout |
| `POST /audit/sessions/users/{user_id}/revoke` | log out everywhere |
| `POST /audit/sessions/expire-stale` | stamp `EXPIRED` on lapsed sessions |

### 10.8 Verification
| Check | Result |
|---|---|
| JWT carries the issued `jti` + `iat` | PASS |
| `create_access_token` defaults a `jti` for callers that don't pass one | PASS |
| Login opens the session row; origin IP captured from context | PASS |
| Live session → request allowed | PASS |
| **Revoked session → request rejected** | PASS |
| `revoked_by_user_id` recorded | PASS |
| Re-closing preserves the original `end_reason` (logout can't mask a revoke) | PASS |
| **Token with no `jti` → allowed** (backward compat) | PASS |
| **`jti` with no session row → allowed** (backward compat) | PASS |
| `end_all_for_user` closes every live session | PASS |
| `expire_stale` stamps `EXPIRED`; naive-datetime rows handled | PASS |
| **DB unreachable → `check_and_touch` fails OPEN** | PASS |
| DB unreachable → `start`/`end` never raise | PASS |
| All three tables join on one key; browser tab id preserved in metadata | PASS |
| Offline DDL render — only `CREATE TABLE`/`CREATE INDEX` for `user_sessions` | PASS |
| Route ordering (`/active`, `/expire-stale`, `/users/{id}/revoke` before `/{session_id}`) | PASS |
| OpenAPI — all 22 pre-existing paths / 33 operations unchanged | PASS |
| `pytest`, full app import (44 routes), pyflakes, single alembic head | PASS |

### 10.9 KNOWN LIMITATION — machine callers (API clients / AI agents / integrations)

**Read this before connecting any non-browser client.** Session tracking was designed for interactive human logins. It is *functionally* correct for machine callers but not *fit for purpose*, and the gap gets worse with volume.

The deployment today has exactly one authentication path — `HTTPBearer` + JWT, 60-minute expiry. There are no API keys and no service accounts. So a machine client has to authenticate by calling `/auth/login` as if it were a person, which is where every problem below comes from.

**What already works correctly**
- A machine login opens a session row and the token carries a `jti`.
- Every API call it makes is audited and correlated to that session.
- Revoking the session blocks it, exactly as for a human.
- `/chat/` calls from an AI agent produce `AI_RAG` audit rows with the session id attached.

**What does not**

| # | Problem | Consequence |
|---|---|---|
| 1 | A client that logs in per request (the common naive integration) creates one session row per request | At 10 req/s that is **~864,000 rows/day**. Even a well-behaved client that caches its token re-logins hourly = 24 rows/day/client, forever |
| 2 | Machines never call `/auth/logout` | Every session ends as `EXPIRED` and accumulates; only `SESSION_RETENTION_DAYS` (180) eventually clears it |
| 3 | `GET /audit/sessions/active` mixes humans and machines | "Who is logged in right now" stops being a meaningful answer once integrations outnumber staff |
| 4 | The revocation check is one indexed read **per request** | Negligible for human traffic; real, sustained load on the primary at AI-agent QPS. Mitigation available today: `SESSION_REVOCATION_CHECK_ENABLED=False` |

**The underlying design gap:** a human and a machine are currently indistinguishable in the audit trail — both appear as `role=CUSTOMER` with an email. But *"a user deleted this document"* and *"an integration deleted this document"* are different events with different escalation paths. NIST SP 800-53 AU-3 and ISO 27001 both expect the actor **type** to be recorded, not just the actor. Enterprise systems never model human sessions and machine identity as the same concept: humans get an interactive session (short TTL, explicit logout, "who's online"), machines get a service account or a `client_credentials` token.

**Migration path when integrations arrive** (deliberately deferred, recorded here so the decision is informed rather than rediscovered):

1. *Small, fully additive:* a `typ` claim on the token (`INTERACTIVE` | `SERVICE` | `AGENT`) plus a `session_type` column on `user_sessions` and `audit_logs`. Lets machines be filtered out of the active-sessions view, makes human-vs-machine actions filterable in the audit log, and allows a separate retention policy for machine sessions. No new auth path; a token without the claim defaults to `INTERACTIVE`, so nothing existing breaks.
2. *Full:* a `service_accounts` table with hashed API keys and an `X-API-Key` dependency alongside the existing JWT path. Machines stop logging in altogether, which removes problem #1 and #2 at the root rather than mitigating them.

Until one of those exists, treat `user_sessions` as a **human-login** table and keep non-browser clients to a token-caching pattern (one login per hour, not per request).

### 10.10 Deployment notes
- `alembic upgrade head` starts the table empty. Every token already in circulation lacks a `jti`, and the service fails open on both a missing `jti` and a missing row — **nobody is signed out by running the migration.** Sessions start being recorded at the next login.
- **No frontend change required.** The backend reads `jti` from the token, so nothing needs to be sent. A revoked user's next request returns 401 and the existing axios response interceptor already redirects to `/login`.
- Rollback: `SESSION_TRACKING_ENABLED=False`, or `alembic downgrade d2e3f4a5b6c7`.

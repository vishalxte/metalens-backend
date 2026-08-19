# Activity + Audit Logs Redesign — Analysis & Plan

**Status:** ANALYSIS COMPLETE — no code changed yet. Three decisions needed (§6).

---

## 1. What exists today

| Component | File | Notes |
|---|---|---|
| Activity model | `app/models/activity_log.py` | 18 columns, 6 activity types |
| Audit model | `app/models/audit_log.py` | 21 columns, 9 categories, ~40 actions |
| Activity repo | `app/repositories/activity_repository.py` | incl. `time_spent_per_page()`, `feature_usage_counts()` |
| Audit repo | `app/repositories/audit_repository.py` | 14 filters, CSV export, `count_by_category()` |
| Activity service | `app/services/activity_service.py` | batch ingest, click gating, session start/end |
| Audit service | `app/services/audit_service.py` | 7 typed helpers, sanitizer, isolated session |
| Activity API | `app/api/v1/activity.py` | 7 routes |
| Audit API | `app/api/v1/audit.py` | 5 routes |
| Sessions API | `app/api/v1/sessions.py` | reads `audit_logs.session_id` |
| Migrations | `c1d2e3f4a5b6` (audit), `d2e3f4a5b6c7` (activity) | both applied |
| Frontend | `src/api/activityTracker.js`, `src/hooks/useActivityTracking.js` | + `AuthContext.jsx`, 3 dashboards |

**Emitters:** 41 `audit_service.*` call sites across 8 files. Activity is emitted only by the frontend.

---

## 2. Target schema

### activity_logs — 18 columns → 9
```
id, event_time, user_id, session_id, action, page, from_page, feature, question_text
```
Actions: `PAGE_VIEW`, `NAVIGATION`, `CLICK`, `QUESTION_SUBMITTED`

### audit_logs — 21 columns → 10
```
id, event_time, user_id, session_id, category, action, target_type, target_id, status, details
```

---

## 3. Dependency scan — what removal actually costs

### 3.1 BLOCKER: 8 audit events have no `user_id` at all

These are identified **only** by `user_email` and/or `ip_address`. Both are on the removal list.

| Event | Emitted from | Identified today by |
|---|---|---|
| `LOGIN_FAILED` (unknown email) | `auth_service.py` | `user_email` only |
| `OTP_SEND_FAILED` | `auth_service.py` | `user_email` only |
| `OTP_FAILED` ×3 | `auth_service.py` | `user_email` only |
| `AUTHENTICATION_FAILURE` | `dependencies/auth.py` | `user_email` only |
| `INACTIVE_ACCOUNT_ACCESS` | `dependencies/auth.py` | `user_email` only |
| `INVALID_TOKEN` (no `sub`) | `dependencies/auth.py` | **`ip_address` only** |
| `JWT_FAILURE` | `dependencies/auth.py` | **`ip_address` only** |

Dropping both columns turns every one of these into `user_id = NULL, action = X` with **no indication of who was attacked or from where**. A failed-login trail that cannot name the targeted account is not a trail.

The spec's own escape clause applies: *"unless you discover that an existing critical functionality absolutely depends on one of them."* It does. Also note the spec lists `SECURITY` as a required category while removing every column those events depend on.

### 3.2 Features deleted by the 4-action list

The spec's actions exclude `SESSION_START`, `SESSION_END`, `FEATURE_USAGE`, and remove `duration_ms`. That removes:

- `ActivityRepository.time_spent_per_page()` — the "Time Spent Per Page" aggregate
- `ActivityRepository.feature_usage_counts()`
- `GET /activity/summary` (whole endpoint)
- `POST /activity/session/start`, `POST /activity/session/end`
- `ActivityService.start_session()` / `end_session()`
- Frontend: `startSession()` / `endSession()` and their calls in `AuthContext.jsx`

### 3.3 Audit actions with no home in the new action list

`DOCUMENT_UPLOADED`, `DOCUMENT_DELETED`, `DOCUMENT_PARSED`, `CHUNKS_CREATED`, `EMBEDDINGS_CREATED`, `INDEXING_FAILED`, `CACHE_CLEARED`, `SESSION_REVOKED`, `PERMISSION_DENIED`, `INVALID_TOKEN`, `JWT_FAILURE`, `AUTHENTICATION_FAILURE`, `INACTIVE_ACCOUNT_ACCESS`, `OTP_*` — 11 call sites.

The spec says "use only the required audit actions" but also "preserve all existing functionality" and "do not change document processing behaviour".

### 3.4 API contract changes (unavoidable)

Removing columns necessarily changes these response schemas:

- `AuditLogResponse` — 11 fields removed
- `ActivityLogResponse` — 9 fields removed
- `AuditRepository._apply_filters` — 7 of 14 filters lose their column (`user_email`, `role`, `resource`, `resource_id`, `request_id`, `ip_address`, plus `search` over `http_path`)
- `GET /audit/logs`, `/audit/export`, `/audit/summary` query params
- `EXPORT_COLUMNS` in `audit.py` — CSV layout
- `GET /audit/sessions/{id}` — embeds `AuditLogResponse`

Frontend does not consume these yet, so no UI breaks.

### 3.5 Safe to remove — confirmed no other dependency

`role`, `customer_id`, `http_method`, `http_path`, `http_status`, `created_at`, `client_timestamp`, `metadata_json`, `user_agent` (audit).
`customer_id` FKs exist on both tables but nothing filters on them.

---

## 4. Migration approach

Two revisions, chained after `b7c8d9e0f1a2`:

1. **activity_logs** — rename `timestamp`→`event_time`, drop 9 columns, add `question_text`, rebuild indexes.
2. **audit_logs** — rename `timestamp`→`event_time`, rename `resource`→`target_type` and `resource_id`→`target_id` (preserves existing data — `RENAME COLUMN`, not drop/add), drop the rest, rebuild indexes.

`target_id` stays `VARCHAR` — `resource_id` already holds document filenames and session UUIDs, not just integers. Converting to INTEGER would fail on existing rows.

**Data loss is unavoidable** for dropped columns. Current volume: 65 audit rows, ~3 activity rows — all development data.

---

## 5. New emitters required

Nothing currently emits `QUESTION_SUBMITTED` or `CLICK`.

- `QUESTION_SUBMITTED` — frontend `ChatPanel` on submit, or backend from `chat.py`. Backend is more reliable (can't be skipped by a client), and `chat.py` already has the question.
- `CLICK` — currently gated off by `ACTIVITY_CLICK_TRACKING_ENABLED=False`; must be enabled and call sites added.

---

## 6. DECISIONS NEEDED

**A. Security-event attribution** — keep `user_email` + `ip_address` on `audit_logs` (2 of the 11 columns), or drop them and accept that failed logins and token abuse become unattributable?

**B. Document / knowledge-base audit events** — keep them (as `category=DOCUMENT_MANAGEMENT`, `target_type=DOCUMENT`), or delete those 11 call sites?

**C. Activity session tracking** — confirm deletion of "Time Spent Per Page", `/activity/summary`, and the session start/end endpoints + frontend calls?

---

## 7. Warning — `question_text` in plaintext

Storing full question text reverses the earlier confidentiality rule and has two consequences worth accepting explicitly:

1. `pg_dump` now writes user questions in plaintext to `D:\backups\metalens`.
2. `activity_logs` has no sanitizer — whatever the user typed is stored verbatim, including anything sensitive they paste into chat.

`audit_logs` will continue to store only `question_length` + `question_sha256` for the AI event.

from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.users import router as user_router
from app.api.v1.documents import router as document_router
from app.api.v1.chat import router as chat_router
from app.api.v1.conversations import router as conversation_router
from app.api.v1.customer import router as customer_router
from app.api.v1.company_users import router as company_users_router

# Audit Framework (Module 1) and Frontend Activity Tracking (Module 2).
# Both are entirely new prefixes (/audit, /activity) — no existing route
# path changes, so the current frontend is unaffected.
from app.api.v1.audit import router as audit_router
from app.api.v1.activity import router as activity_router
from app.api.v1.sessions import router as sessions_router


api_router = APIRouter()

api_router.include_router(auth_router)
api_router.include_router(user_router)
api_router.include_router(customer_router)
api_router.include_router(company_users_router)
api_router.include_router(document_router)
api_router.include_router(chat_router)
api_router.include_router(conversation_router)
api_router.include_router(audit_router)
# Registered BEFORE the activity router and AFTER audit: its paths live
# under /audit/sessions, which never collides with /audit/logs/{id}
# because the first segment after /audit differs.
api_router.include_router(sessions_router)
api_router.include_router(activity_router)
from app.models.customer import Customer
from app.models.user import User
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.embedding import Embedding
from app.models.conversation import Conversation
from app.models.chat_message import ChatMessage
from app.models.semantic_cache import SemanticCache
from app.models.exact_cache import ExactCache

# Audit Framework (Module 1) and Frontend Activity Tracking (Module 2).
# Two independent tables — see the module docstrings for why they are
# never merged.
from app.models.audit_log import AuditLog
from app.models.activity_log import ActivityLog

# Login session lifecycle — joins to the two above on session_id.
from app.models.user_session import UserSession

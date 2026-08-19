from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException

from sqlalchemy.orm import Session

from app.database.session import (
    get_db
)

from app.schemas.chat import (
    ChatRequest,
    ChatResponse
)

from app.repositories.embedding_repository import (
    EmbeddingRepository
)

from app.repositories.conversation_repository import (
    ConversationRepository
)

from app.repositories.chat_message_repository import (
    ChatMessageRepository
)

from app.models.conversation import Conversation
from app.models.chat_message import ChatMessage

from app.services.rag_service import (
    RAGService
)

from app.services.semantic_cache_service import (
    SemanticCacheService
)

from app.api.dependencies.auth import (
    get_customer_user
)

from app.core.request_context import (
    get_request_id,
    get_current_super_admin_id
)

from app.core.chat_summary import log_chat_summary
from app.core.logging import logger

# Audit Framework — AI / RAG category.
from app.models.audit_log import AuditAction
from app.services.audit_service import audit_service

# Activity: the question itself is recorded here, not in audit_logs.
from app.models.activity_log import ActivityPage
from app.services.activity_service import activity_service

import hashlib

_QUESTION_LOG_SEPARATOR = "*" * 70

router = APIRouter(
    prefix="/chat",
    tags=["Chat"]
)


def _make_title(question: str) -> str:
    """
    Derives a short sidebar title from the first question in a
    conversation, the same way ChatGPT titles new chats from your first
    message. Kept as a simple truncation — no extra LLM call spent just
    on generating a title.
    """
    title = question.strip()
    max_len = 50

    if len(title) > max_len:
        title = title[:max_len].rstrip() + "..."

    return title or "New Chat"


@router.post(
    "/",
    response_model=ChatResponse
)
def chat(
    payload: ChatRequest,
    db: Session = Depends(
        get_db
    ),
    current_user=Depends(
        get_customer_user
    )
):

    embedding_repo = EmbeddingRepository(db)
    conversation_repo = ConversationRepository(db)
    message_repo = ChatMessageRepository(db)

    semantic_cache = SemanticCacheService(
        db
    )

    service = RAGService(
        embedding_repo,
        semantic_cache
    )

    # 1. Resolve which conversation this message belongs to. If the
    # frontend sent no conversation_id, this is the first message of a
    # brand-new chat (e.g. user typed straight into a fresh "New Chat")
    # — create it now.
    if payload.conversation_id:
        conversation = conversation_repo.get_by_id_for_owner(
            payload.conversation_id,
            current_user.id,
            current_user.customer_id
        )

        if not conversation:
            raise HTTPException(
                status_code=404,
                detail="Conversation not found"
            )
    else:
        conversation = conversation_repo.create(
            Conversation(
                title="New Chat",
                owner_id=current_user.id,
                customer_id=current_user.customer_id
            )
        )

    # 2. Load recent history straight from the DB — NOT from the client —
    # so follow-up condensing always works off the real, tamper-proof
    # record of this conversation instead of whatever the frontend sends.
    recent_messages = message_repo.get_recent_for_conversation(
        conversation.id,
        limit=6
    )

    chat_history = [
        {
            "role": m.role,
            "content": m.content
        }
        for m in recent_messages
    ]

    # 3. Auto-title: if this is the first message in the conversation,
    # derive a short title from it. Covers both a brand-new conversation
    # created above, and an empty one pre-created via the sidebar's
    # "New Chat" button before any message was sent.
    if not recent_messages:
        conversation_repo.update_title(
            conversation,
            _make_title(payload.question)
        )

    # 4. Persist the user's message before calling the LLM, so it's saved
    # even if something downstream (LLM call, retrieval) fails.
    message_repo.create(
        ChatMessage(
            conversation_id=conversation.id,
            role="user",
            content=payload.question
        )
    )

    # ─────────────────────────────────────────
    # ACTIVITY: QUESTION_SUBMITTED.
    #
    # Emitted SERVER-SIDE, before the LLM call, and deliberately not
    # from the browser. The backend already has the question, so
    # recording it here means it cannot be skipped by a client that
    # chooses not to send it, and it is captured even if generation
    # subsequently fails — "what did the user ask?" should not depend on
    # whether the answer succeeded.
    #
    # This is the ONE place the question text is stored. The AI audit
    # row below still keeps only a length and a hash.
    #
    # Never raises (see ActivityService), so it cannot affect the chat.
    # ─────────────────────────────────────────
    activity_service.track_question(
        question_text=payload.question,
        page=ActivityPage.CHAT
    )

    result = service.ask(
        payload.question,
        customer_id=current_user.customer_id,
        chat_history=chat_history
    )

    # 5. Persist the assistant's answer (with sources), and bump the
    # conversation so it floats to the top of the sidebar.
    message_repo.create(
        ChatMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=result["answer"],
            sources=result["sources"]
        )
    )

    conversation_repo.touch(conversation)

    result["conversation_id"] = conversation.id

    # Pull off the diagnostics RAGService.ask() attached (see rag_service.py)
    # to render the one-box-per-request audit summary — pop it first so the
    # API response shape sent to the frontend is exactly what it was before
    # this logging feature (no functional/response change).
    diagnostics = result.pop("diagnostics", {})
    token_usage = result.get("token_usage", {})

    log_chat_summary(
        request_id=get_request_id(),
        customer_id=current_user.customer_id,
        super_admin_id=get_current_super_admin_id(),
        user_email=current_user.email,
        user_role=current_user.role,
        conversation_id=conversation.id,
        question=payload.question,
        exact_cache_status=diagnostics.get("exact_cache_status", "-"),
        semantic_cache_status=diagnostics.get("semantic_cache_status", "-"),
        vector_chunks=diagnostics.get("vector_chunks"),
        keyword_chunks=diagnostics.get("keyword_chunks"),
        merged_chunks=diagnostics.get("merged_chunks"),
        embedding_model=diagnostics.get("embedding_model", "-"),
        llm_model=diagnostics.get("llm_model", "-"),
        prompt_tokens=token_usage.get("prompt_tokens", 0),
        completion_tokens=token_usage.get("completion_tokens", 0),
        total_tokens=token_usage.get("total_tokens", 0),
        embedding_time_sec=diagnostics.get("embedding_time_sec"),
        llm_time_sec=diagnostics.get("llm_time_sec"),
        total_time_sec=diagnostics.get("total_time_sec", 0),
        exact_cache_written=diagnostics.get("exact_cache_written", False),
        semantic_cache_written=diagnostics.get("semantic_cache_written", False),
        chat_history_saved=True,
        # Required by build_summary_box(). This belongs to the
        # pre-existing chat_summary log file (logs/chat_summary.log) and
        # is unrelated to audit_logs — the audit table's http_status
        # column was removed, this human-readable summary box still
        # prints the response code.
        http_status=200
    )

    # ─────────────────────────────────────────
    # AI / RAG audit record.
    #
    # LAYERING NOTE: audit calls normally live in a business service, not
    # a route. This is the one deliberate exception. `diagnostics` only
    # exists here — RAGService returns it and this route pops it off
    # before responding — and conversation_id is resolved here too, not
    # inside RAGService. Pushing the call down into RAGService would mean
    # changing its signature to accept a conversation_id it has no other
    # use for, which the brief explicitly rules out.
    #
    # Built entirely from `diagnostics` and `token_usage`, which
    # RAGService already produced and which log_chat_summary() above
    # already consumes — so this adds no new instrumentation to
    # RAGService, no extra LLM/DB work, and no change to `result`.
    #
    # CONFIDENTIALITY: the question text, the generated answer and every
    # retrieved chunk are deliberately absent. Only a length and a
    # SHA-256 digest of the question are stored — enough to correlate
    # repeat questions and to prove a specific question was asked if one
    # is ever produced during an investigation, without the audit table
    # ever holding the question itself.
    # ─────────────────────────────────────────
    question_digest = hashlib.sha256(
        (payload.question or "").strip().lower().encode("utf-8")
    ).hexdigest()

    audit_service.log_ai_event(
        action=AuditAction.RESPONSE_GENERATED,
        conversation_id=conversation.id,
        details={
            "question_length": len(payload.question or ""),
            "question_sha256": question_digest,
            "embedding_model": diagnostics.get("embedding_model"),
            "llm_model": diagnostics.get("llm_model"),
            "exact_cache_status": diagnostics.get("exact_cache_status"),
            "semantic_cache_status": diagnostics.get("semantic_cache_status"),
            "hybrid_search_used": diagnostics.get("vector_chunks") is not None,
            "vector_chunks": diagnostics.get("vector_chunks"),
            "keyword_chunks": diagnostics.get("keyword_chunks"),
            "final_chunks": diagnostics.get("merged_chunks"),
            "sources_returned": len(result.get("sources") or []),
            "prompt_tokens": token_usage.get("prompt_tokens", 0),
            "completion_tokens": token_usage.get("completion_tokens", 0),
            "total_tokens": token_usage.get("total_tokens", 0),
            "embedding_time_sec": diagnostics.get("embedding_time_sec"),
            "llm_time_sec": diagnostics.get("llm_time_sec"),
            "total_time_sec": diagnostics.get("total_time_sec"),
            "exact_cache_written": diagnostics.get("exact_cache_written", False),
            "semantic_cache_written": diagnostics.get("semantic_cache_written", False),
            "conversation_id": conversation.id
        }
    )

    # Visually separates one question's full log trail (auth, cache,
    # retrieval, LLM, etc. — all interleaved with other requests in
    # app.log) from the next, so scanning the log file question-by-
    # question doesn't require grepping the request_id every time.
    logger.info(_QUESTION_LOG_SEPARATOR)

    return result

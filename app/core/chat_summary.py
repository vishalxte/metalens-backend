"""
Human-readable, one-box-per-request audit log for every chat/RAG
request — separate from the structured app.log stream, written to its
own rotating file (logs/chat_summary.log) so it's easy to scan/grep on
its own without the volume of every other DEBUG/INFO log line.

This does NOT replace the structured logging added across
rag_service/llm_service/embedding_service/cache_service — those still
log each individual step as before. This module only renders one final
summary box per chat request, using the pieces those steps already
computed (via RAGService.ask()'s "diagnostics" dict — see rag_service.py)
plus request-level facts only the route handler knows (conversation id,
which user, chat history saved, HTTP status).
"""
import logging
import logging.handlers
import os
from datetime import datetime

from app.core.config import settings

_LOGGER_NAME = "ai_document_search.chat_summary"
_BOX_WIDTH = 67

chat_summary_logger = logging.getLogger(_LOGGER_NAME)


def _setup_chat_summary_logger() -> None:
    """
    Idempotent, mirrors setup_logging()'s pattern in app/core/logging.py.
    Deliberately propagate=False so this box doesn't ALSO get duplicated
    into app.log/console via the root "ai_document_search" logger.
    """
    chat_summary_logger.setLevel(logging.INFO)
    chat_summary_logger.handlers.clear()
    chat_summary_logger.propagate = False

    try:
        os.makedirs(settings.LOG_DIR, exist_ok=True)
        file_path = os.path.join(settings.LOG_DIR, "chat_summary.log")
        handler = logging.handlers.RotatingFileHandler(
            file_path,
            maxBytes=settings.LOG_MAX_BYTES,
            backupCount=settings.LOG_BACKUP_COUNT,
            encoding="utf-8"
        )
        # Plain passthrough formatter — the box text itself is already
        # fully formatted, no extra timestamp/level prefix needed.
        handler.setFormatter(logging.Formatter("%(message)s"))
        chat_summary_logger.addHandler(handler)
    except OSError:
        chat_summary_logger.addHandler(logging.StreamHandler())


_setup_chat_summary_logger()


def _row(label: str, value) -> str:
    return f"{label:<15}: {value}"


def build_summary_box(
    *,
    request_id,
    customer_id,
    super_admin_id,
    user_email,
    user_role,
    conversation_id,
    question,
    exact_cache_status,
    semantic_cache_status,
    vector_chunks=None,
    keyword_chunks=None,
    merged_chunks=None,
    embedding_model,
    llm_model,
    prompt_tokens=0,
    completion_tokens=0,
    total_tokens=0,
    embedding_time_sec=None,
    llm_time_sec=None,
    total_time_sec,
    exact_cache_written,
    semantic_cache_written,
    chat_history_saved,
    http_status
) -> str:
    """
    Renders the exact boxed report format:

        ═══════════════════════════════════════════════════════════════
        AI CHAT REQUEST SUMMARY
        ═══════════════════════════════════════════════════════════════
        Date           : ...
        ...
        ═══════════════════════════════════════════════════════════════

    Vector/Keyword/Merged/Embedding-Time/LLM-Time rows are only included
    when the corresponding step actually ran (e.g. an exact-cache HIT
    never reaches retrieval or the LLM at all, so those rows are simply
    omitted for that request rather than shown as misleading zeros).
    """
    border = "═" * _BOX_WIDTH

    rows = [
        border,
        "AI CHAT REQUEST SUMMARY",
        border,
        _row("Date", datetime.now().strftime("%d-%b-%Y %H:%M:%S")),
        _row("Request ID", request_id or "-"),
        _row("Customer", customer_id if customer_id is not None else "-"),
        _row("Super Admin ID", super_admin_id if super_admin_id is not None else "-"),
        _row("User", f"{user_email} ({user_role})"),
        _row("Conversation", conversation_id),
        _row("Question", question),
        _row("Authentication", "Success"),
        _row("Conversation", "Verified"),
        _row("Exact Cache", exact_cache_status),
        _row("Semantic Cache", semantic_cache_status),
    ]

    if vector_chunks is not None:
        rows.append(_row("Vector Search", f"{vector_chunks} chunks"))
    if keyword_chunks is not None:
        rows.append(_row("Keyword Search", f"{keyword_chunks} chunks"))
    if merged_chunks is not None:
        rows.append(_row("Merged Results", f"{merged_chunks} unique chunks"))

    rows.append(_row("Embedding Model", embedding_model))
    rows.append(_row("LLM Model", llm_model))
    rows.append(_row("Prompt Tokens", prompt_tokens))
    rows.append(_row("Completion", completion_tokens))
    rows.append(_row("Total Tokens", total_tokens))

    if embedding_time_sec is not None:
        rows.append(_row("Embedding Time", f"{embedding_time_sec:.2f} sec"))
    if llm_time_sec is not None:
        rows.append(_row("LLM Time", f"{llm_time_sec:.2f} sec"))

    rows.append(_row("Total API Time", f"{total_time_sec:.2f} sec"))
    rows.append(_row("Exact Cache", "Written" if exact_cache_written else "Not written"))
    rows.append(_row("Semantic Cache", "Written" if semantic_cache_written else "Not written"))
    rows.append(_row("Chat History", "Saved" if chat_history_saved else "Not saved"))
    rows.append(_row("Status", f"HTTP {http_status}"))
    rows.append(border)

    return "\n".join(rows)


def log_chat_summary(**fields) -> None:
    chat_summary_logger.info(build_summary_box(**fields))

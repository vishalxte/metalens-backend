import re
import time

from app.services.embedding_service import (
    embedding_service,
    EMBEDDING_MODEL
)

from app.services.llm_service import (
    LLMService,
    LLM_MODEL
)

from app.services.cache_service import (
    cache_service
)

from app.core.logging import logger
from app.core.timing import log_duration


# A "meaningful" question must contain at least one run of 2+ letters/digits
# somewhere in it. This is deliberately simple and cheap (no LLM call) — it
# exists purely to catch junk input like "k", ".", "?", "  ", "!!" before we
# waste an embedding + retrieval + LLM call on it, and before it pollutes
# the exact/semantic caches with a garbage entry.
_MEANINGFUL_CONTENT = re.compile(r"[A-Za-z0-9]{2,}")

# NOTE: We deliberately do NOT dump an entire knowledge base into context
# even when it's small enough to technically fit the model's token
# window. That was tried (send-everything below a chunk-count threshold)
# and it backfired: with ~220 chunks (~39k tokens) in context at once,
# gpt-4o-mini returned the flat "not found" fallback even though the
# answer was plainly present in the context — a known LLM limitation
# ("lost in the middle": models reliably use info from the start/end of
# a long context but lose track of things placed deep in the middle).
# Real retrieval, kept generous, is the safer default at any size.


def _is_meaningful_question(question: str) -> bool:
    return bool(_MEANINGFUL_CONTENT.search(question or ""))


def _merge_retrieval_results(vector_results, keyword_results, max_total=25):
    """
    Combines vector-similarity hits with full-text keyword hits into one
    deduplicated list (by chunk_id), vector-ranked results first. A chunk
    found by both is only included once. Capped at `max_total` so a very
    generous vector top_k plus a keyword pass still can't blow up the
    context sent to the LLM on every single question.
    """
    seen_ids = set()
    merged = []

    for row in vector_results:
        if row.chunk_id not in seen_ids:
            seen_ids.add(row.chunk_id)
            merged.append(row)

    for row in keyword_results:
        if row.chunk_id not in seen_ids:
            seen_ids.add(row.chunk_id)
            merged.append(row)

    return merged[:max_total]


def _build_clarification_answer(question: str) -> str:
    """
    Builds a specific, friendly reply for junk input instead of one flat
    canned line. It references exactly what the user sent (a period, a
    single letter, nothing at all) so the reply feels like a real assistant
    response — not an error message — and always ends by inviting an
    actual question about their documents.
    """
    stripped = (question or "").strip()

    if not stripped:
        return (
            "It looks like you sent an empty message. What would you "
            "like to know from your uploaded documents?"
        )

    if not any(char.isalnum() for char in stripped):
        # Pure punctuation/symbols — e.g. ".", "?", "!!", "..."
        return (
            f"It looks like you only sent \"{stripped}\". What would you "
            "like me to look up in your documents?"
        )

    if len(stripped) == 1:
        # A single letter/digit — e.g. "k", "i", "5"
        return (
            f"\"{stripped}\" is a bit too short for me to search your "
            "documents with. Could you ask a fuller question about what you are looking for?"
        )

    # Anything else that still failed the meaningful-content check (e.g.
    # a couple of stray symbols mixed with one letter, like ".k.")
    return (
        f"\"{stripped}\" isn't quite enough for me to work with. Could "
        "you rephrase it as a full question about your documents?"
    )


class RAGService:

    def __init__(
        self,
        embedding_repository,
        semantic_cache_service
    ):

        self.embedding_repository = (
            embedding_repository
        )

        self.embedding_service = (
            embedding_service
        )

        self.llm_service = (
            LLMService()
        )

        self.cache_service = cache_service

        self.semantic_cache_service = semantic_cache_service

    def ask(
        self,
        question,
        customer_id,
        chat_history=None
    ):

        chat_history = chat_history or []
        ask_start = time.time()

        logger.info(
            f"RAG ask() starting — customer_id={customer_id}, question_chars={len(question or '')}, "
            f"history_turns={len(chat_history)}",
            extra={
                "event": "rag_ask_started",
                "customer_id": customer_id,
                "has_history": bool(chat_history)
            }
        )

        # -1. Reject junk input (single letters, stray punctuation, blank
        # strings, etc.) immediately — before condensing, before either
        # cache tier, before retrieval. This is checked on the RAW question
        # exactly as typed, regardless of chat history, because "k" isn't a
        # real question whether or not there was a prior conversation.
        if not _is_meaningful_question(question):
            logger.info(f"Rejected non-meaningful question: '{question}'")
            elapsed_ms = round((time.time() - ask_start) * 1000, 2)
            return {
                "answer": _build_clarification_answer(question),
                "token_usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0
                },
                "sources": [],
                "cache": {"hit": False},
                "diagnostics": {
                    "exact_cache_status": "N/A",
                    "semantic_cache_status": "N/A",
                    "embedding_model": EMBEDDING_MODEL,
                    "llm_model": LLM_MODEL,
                    "exact_cache_written": False,
                    "semantic_cache_written": False,
                    "total_time_sec": elapsed_ms / 1000
                }
            }

        # 0. Follow-up handling: if there's prior conversation, rewrite the
        # question into a standalone one before doing anything else. This
        # standalone version is what gets embedded, cached, and retrieved
        # against — so "what about its price?" becomes a self-contained
        # question and doesn't pollute the cache with ambiguous text.
        if chat_history:
            standalone_question = self.llm_service.condense_question(
                chat_history,
                question
            )
        else:
            standalone_question = question

        # 1. Exact cache (Postgres exact_cache table) — keyed per-customer, see cache_service.py
        exact_hit = self.cache_service.get(standalone_question, customer_id)

        if exact_hit is not None:
            exact_hit["cache"] = {"hit": True, "type": "exact"}
            exact_hit["token_usage"] = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            }
            elapsed_ms = round((time.time() - ask_start) * 1000, 2)
            logger.info(
                f"Question: '{standalone_question}' — served from EXACT cache, "
                f"0 tokens used, {elapsed_ms}ms total",
                extra={"event": "rag_ask_completed", "path": "exact_cache", "duration_ms": elapsed_ms}
            )
            exact_hit["diagnostics"] = {
                "exact_cache_status": "HIT",
                "semantic_cache_status": "N/A",
                "embedding_model": EMBEDDING_MODEL,
                "llm_model": LLM_MODEL,
                "exact_cache_written": False,
                "semantic_cache_written": False,
                "total_time_sec": elapsed_ms / 1000
            }
            return exact_hit

        # Embed once, reuse for both semantic cache lookup and RAG retrieval
        embedding_start = time.time()
        query_embedding = (
            self.embedding_service
            .generate_embedding(
                standalone_question
            )
        )
        embedding_time_sec = round(time.time() - embedding_start, 2)

        # 2. Semantic cache (pgvector), scoped to this customer
        semantic_hit = self.semantic_cache_service.get(query_embedding, customer_id)

        if semantic_hit is not None:
            elapsed_ms = round((time.time() - ask_start) * 1000, 2)
            logger.info(
                f"Question: '{standalone_question}' — served from SEMANTIC cache, {elapsed_ms}ms total",
                extra={"event": "rag_ask_completed", "path": "semantic_cache", "duration_ms": elapsed_ms}
            )
            # Warm the exact cache too, so the next literal repeat of this
            # question hits the exact_cache table directly instead of hitting semantic again.
            self.cache_service.set(standalone_question, semantic_hit, customer_id)
            semantic_hit["diagnostics"] = {
                "exact_cache_status": "MISS",
                "semantic_cache_status": "HIT",
                "embedding_model": EMBEDDING_MODEL,
                "llm_model": LLM_MODEL,
                "embedding_time_sec": embedding_time_sec,
                "exact_cache_written": True,
                "semantic_cache_written": False,
                "total_time_sec": elapsed_ms / 1000
            }
            return semantic_hit

        # 3. Cache miss on both tiers -> RAG retrieval + LLM, scoped to this
        # customer. Hybrid: a generous vector-similarity pass PLUS a
        # full-text keyword pass that catches exact-term matches (dates,
        # specific clauses) a chunk's overall topic might rank outside
        # the vector results even at a generous top_k.
        vector_results = (
            self.embedding_repository
            .similarity_search(
                query_embedding=query_embedding,
                customer_id=customer_id,
                top_k=40
            )
        )

        keyword_results = (
            self.embedding_repository
            .keyword_search(
                question=standalone_question,
                customer_id=customer_id,
                limit=20
            )
        )

        results = _merge_retrieval_results(
            vector_results, keyword_results, max_total=60
        )

        logger.info(
            f"Question: '{standalone_question}' — retrieved "
            f"{len(vector_results)} vector + {len(keyword_results)} keyword "
            f"hits, {len(results)} unique chunks after merge: "
            + ", ".join(
                f"[{row.filename} | dist={row.distance if row.distance is not None else 'kw'} | "
                f"{row.chunk_text[:60]!r}...]"
                for row in results
            )
        )

        # NOTE: We deliberately do NOT expose the real filename to the LLM
        # here. Only a generic, non-identifying label ("Document N") is
        # included in the context that gets sent to the model. This is what
        # actually stops the model from ever mentioning/citing filenames or
        # document names inside the generated answer text — a prompt
        # instruction alone isn't reliable if the filename is sitting right
        # there in the context. The real filename is still tracked below in
        # `sources`, purely for our own app-side bookkeeping/UI/cache — it
        # is never derived from anything the model outputs.
        context = "\n\n".join(
            [
                f"[Document {idx + 1}]\n{row.chunk_text}"
                for idx, row in enumerate(results)
            ]
        )

        llm_start = time.time()
        result = (
            self.llm_service
            .generate_answer(
                context=context,
                question=standalone_question
            )
        )
        llm_time_sec = round(time.time() - llm_start, 2)

        answer = result["answer"]
        token_usage = result["token_usage"]

        sources = [
            {
                "filename": row.filename,
                "excerpt": row.chunk_text[:200] + "..."
                if len(row.chunk_text) > 200
                else row.chunk_text,
                # Keyword-only hits have no vector distance to score —
                # they were matched by exact/stemmed word match instead.
                "relevance_score": (
                    round(1 - row.distance, 4)
                    if row.distance is not None
                    else None
                )
            }
            for row in results
        ]

        response = {
            "answer": answer,
            "token_usage": token_usage,
            "sources": sources,
            "cache": {"hit": False}
        }

        # 4. Save to the exact cache + Semantic Cache, keyed on the standalone
        # question AND this customer, so tenants never share cached answers.
        self.cache_service.set(standalone_question, response, customer_id)

        self.semantic_cache_service.set(
            question=standalone_question,
            question_embedding=query_embedding,
            answer=answer,
            sources=sources,
            customer_id=customer_id
        )

        elapsed_ms = round((time.time() - ask_start) * 1000, 2)
        logger.info(
            f"Question: '{standalone_question}' — full retrieval+LLM generation, {elapsed_ms}ms total",
            extra={
                "event": "rag_ask_completed",
                "path": "full_generation",
                "duration_ms": elapsed_ms,
                "chunks_used": len(results),
                **token_usage
            }
        )

        response["diagnostics"] = {
            "exact_cache_status": "MISS",
            "semantic_cache_status": "MISS",
            "vector_chunks": len(vector_results),
            "keyword_chunks": len(keyword_results),
            "merged_chunks": len(results),
            "embedding_model": EMBEDDING_MODEL,
            "llm_model": LLM_MODEL,
            "embedding_time_sec": embedding_time_sec,
            "llm_time_sec": llm_time_sec,
            "exact_cache_written": True,
            "semantic_cache_written": True,
            "total_time_sec": elapsed_ms / 1000
        }

        return response

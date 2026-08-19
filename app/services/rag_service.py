import re
import time

from app.services.embedding_service import embedding_service, EMBEDDING_MODEL
from app.services.llm_service import LLMService, LLM_MODEL
from app.services.cache_service import cache_service
from app.core.logging import logger

_MEANINGFUL_CONTENT = re.compile(r"[A-Za-z0-9]{2,}")


def _is_meaningful_question(question: str) -> bool:
    return bool(_MEANINGFUL_CONTENT.search(question or ""))


def _merge_retrieval_results(vector_results, keyword_results, max_total=25):
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
    stripped = (question or "").strip()
    if not stripped:
        return "It looks like you sent an empty message. What would you like to know from your uploaded documents?"
    if not any(char.isalnum() for char in stripped):
        return f'It looks like you only sent "{stripped}". What would you like me to look up in your documents?'
    if len(stripped) == 1:
        return f'"{stripped}" is a bit too short for me to search your documents with. Could you ask a fuller question about what you are looking for?'
    return f'"{stripped}" isn\'t quite enough for me to work with. Could you rephrase it as a full question about your documents?'


class RAGService:
    def __init__(self, embedding_repository, semantic_cache_service):
        self.embedding_repository = embedding_repository
        self.embedding_service = embedding_service
        self.llm_service = LLMService()
        self.cache_service = cache_service
        self.semantic_cache_service = semantic_cache_service

    def ask(self, question, customer_id, chat_history=None):
        chat_history = chat_history or []
        ask_start = time.time()
        logger.info(
            f"RAG ask() starting — customer_id={customer_id}, question_chars={len(question or '')}, history_turns={len(chat_history)}",
            extra={"event": "rag_ask_started", "customer_id": customer_id, "has_history": bool(chat_history)}
        )

        if not _is_meaningful_question(question):
            # Never log the raw question; it is sensitive application data.
            logger.info("Rejected non-meaningful question")
            elapsed_ms = round((time.time() - ask_start) * 1000, 2)
            return {
                "answer": _build_clarification_answer(question),
                "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "sources": [], "cache": {"hit": False},
                "diagnostics": {"exact_cache_status": "N/A", "semantic_cache_status": "N/A",
                                "embedding_model": EMBEDDING_MODEL, "llm_model": LLM_MODEL,
                                "exact_cache_written": False, "semantic_cache_written": False,
                                "total_time_sec": elapsed_ms / 1000}
            }

        if chat_history:
            standalone_question = self.llm_service.condense_question(chat_history, question)
        else:
            standalone_question = question

        exact_hit = self.cache_service.get(standalone_question, customer_id)
        if exact_hit is not None:
            exact_hit["cache"] = {"hit": True, "type": "exact"}
            exact_hit["token_usage"] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            elapsed_ms = round((time.time() - ask_start) * 1000, 2)
            logger.info("RAG request served from exact cache", extra={"event": "rag_ask_completed", "path": "exact_cache", "duration_ms": elapsed_ms})
            exact_hit["diagnostics"] = {"exact_cache_status": "HIT", "semantic_cache_status": "N/A",
                                          "embedding_model": EMBEDDING_MODEL, "llm_model": LLM_MODEL,
                                          "exact_cache_written": False, "semantic_cache_written": False,
                                          "total_time_sec": elapsed_ms / 1000}
            return exact_hit

        embedding_start = time.time()
        query_embedding = self.embedding_service.generate_embedding(standalone_question)
        embedding_time_sec = round(time.time() - embedding_start, 2)

        semantic_hit = self.semantic_cache_service.get(query_embedding, customer_id)
        if semantic_hit is not None:
            elapsed_ms = round((time.time() - ask_start) * 1000, 2)
            logger.info("RAG request served from semantic cache", extra={"event": "rag_ask_completed", "path": "semantic_cache", "duration_ms": elapsed_ms})
            self.cache_service.set(standalone_question, semantic_hit, customer_id)
            semantic_hit["diagnostics"] = {"exact_cache_status": "MISS", "semantic_cache_status": "HIT",
                                            "embedding_model": EMBEDDING_MODEL, "llm_model": LLM_MODEL,
                                            "embedding_time_sec": embedding_time_sec, "exact_cache_written": True,
                                            "semantic_cache_written": False, "total_time_sec": elapsed_ms / 1000}
            return semantic_hit

        vector_results = self.embedding_repository.similarity_search(query_embedding=query_embedding, customer_id=customer_id, top_k=40)
        keyword_results = self.embedding_repository.keyword_search(question=standalone_question, customer_id=customer_id, limit=20)
        results = _merge_retrieval_results(vector_results, keyword_results, max_total=60)

        # Log counts only. Chunk text, filenames and questions are sensitive.
        logger.info(
            "RAG retrieval completed",
            extra={"event": "rag_retrieval_completed", "vector_hits": len(vector_results),
                   "keyword_hits": len(keyword_results), "merged_hits": len(results)}
        )

        context = "\n\n".join(
            f"[Document {idx + 1}]\n{row.chunk_text}" for idx, row in enumerate(results)
        )
        llm_start = time.time()
        result = self.llm_service.generate_answer(context=context, question=standalone_question)
        llm_time_sec = round(time.time() - llm_start, 2)

        answer = result["answer"]
        token_usage = result["token_usage"]
        sources = [{
            "filename": row.filename,
            "excerpt": row.chunk_text[:200] + "..." if len(row.chunk_text) > 200 else row.chunk_text,
            "relevance_score": round(1 - row.distance, 4) if row.distance is not None else None
        } for row in results]

        response = {"answer": answer, "token_usage": token_usage, "sources": sources, "cache": {"hit": False}}
        self.cache_service.set(standalone_question, response, customer_id)
        self.semantic_cache_service.set(question=standalone_question, question_embedding=query_embedding,
                                        answer=answer, sources=sources, customer_id=customer_id)

        elapsed_ms = round((time.time() - ask_start) * 1000, 2)
        logger.info("RAG request completed", extra={"event": "rag_ask_completed", "path": "full_generation",
                                                     "duration_ms": elapsed_ms, "chunks_used": len(results), **token_usage})
        response["diagnostics"] = {
            "exact_cache_status": "MISS", "semantic_cache_status": "MISS",
            "vector_chunks": len(vector_results), "keyword_chunks": len(keyword_results),
            "merged_chunks": len(results), "embedding_model": EMBEDDING_MODEL, "llm_model": LLM_MODEL,
            "embedding_time_sec": embedding_time_sec, "llm_time_sec": llm_time_sec,
            "exact_cache_written": True, "semantic_cache_written": True,
            "total_time_sec": elapsed_ms / 1000
        }
        return response

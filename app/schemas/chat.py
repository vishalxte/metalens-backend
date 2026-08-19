from typing import List
from typing import Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):

    question: str

    # If omitted, the backend creates a brand-new conversation and returns
    # its id in the response, so the frontend can start tracking it.
    conversation_id: Optional[int] = None


class SourceItem(BaseModel):

    filename: str
    excerpt: str
    # None for chunks found only by the full-text keyword pass — they
    # were never scored against the question's embedding, so there's no
    # cosine-similarity number to report for them.
    relevance_score: Optional[float] = None


class CacheInfo(BaseModel):

    hit: bool
    type: Optional[str] = None
    similarity: Optional[float] = None
    matched_question: Optional[str] = None


class TokenUsage(BaseModel):

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatResponse(BaseModel):

    conversation_id: int
    answer: str
    token_usage: Optional[TokenUsage] = None
    sources: List[SourceItem]
    cache: CacheInfo
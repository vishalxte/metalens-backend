from typing import List
from typing import Optional
from datetime import datetime

from pydantic import BaseModel


class ConversationCreate(BaseModel):
    """
    Body for creating a new conversation. Title is optional — if omitted,
    the backend auto-generates one from the first question asked in it
    (handled in the chat route, not here).
    """
    title: Optional[str] = None


class ConversationSummary(BaseModel):
    """
    One row in the sidebar list — deliberately lightweight (no messages),
    since the sidebar only needs id/title/timestamps to render.
    """
    id: int
    title: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class MessageSource(BaseModel):
    filename: str
    excerpt: str
    # None for chunks found only by the full-text keyword pass (no
    # vector-distance to score them by) — see app/schemas/chat.py's
    # SourceItem for the same field and why it's optional.
    relevance_score: Optional[float] = None


class MessageOut(BaseModel):
    """
    A single stored message, returned when a past conversation is opened
    from the sidebar. `sources` is only populated for assistant messages.
    """
    id: int
    role: str
    content: str
    sources: Optional[List[MessageSource]] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ConversationDetail(BaseModel):
    """
    Full conversation payload — used when the user clicks a past
    conversation in the sidebar and the frontend needs to hydrate the
    main chat view with everything that was said in it.
    """
    id: int
    title: str
    messages: List[MessageOut]

    class Config:
        from_attributes = True
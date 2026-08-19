from sqlalchemy.orm import Session

from app.models.chat_message import ChatMessage


class ChatMessageRepository:

    def __init__(self, db: Session):
        self.db = db

    def create(self, message: ChatMessage):
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        return message

    def get_all_for_conversation(self, conversation_id: int):
        """
        Full message list for a conversation, oldest first — used when the
        sidebar loads a past conversation into the main chat view.
        """
        return (
            self.db.query(ChatMessage)
            .filter(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at.asc())
            .all()
        )

    def get_recent_for_conversation(self, conversation_id: int, limit: int = 6):
        """
        Last `limit` messages, used to build the chat_history passed into
        LLMService.condense_question(). Fetched newest-first for an
        efficient LIMIT query, then reversed back to chronological order
        before returning, since condense_question expects oldest -> newest.
        """
        rows = (
            self.db.query(ChatMessage)
            .filter(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
            .all()
        )

        return list(reversed(rows))
from sqlalchemy.orm import Session

from app.models.conversation import Conversation


class ConversationRepository:

    def __init__(self, db: Session):
        self.db = db

    def create(self, conversation: Conversation):
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        return conversation

    def get_by_id(self, conversation_id: int):
        return (
            self.db.query(Conversation)
            .filter(Conversation.id == conversation_id)
            .first()
        )

    def get_by_id_for_owner(self, conversation_id: int, owner_id: int, customer_id: int):
        """
        Same as get_by_id but also scopes to the owner AND the tenant, so
        one user can never fetch/rename/delete another user's conversation
        just by guessing an ID — and, as defense-in-depth, a mismatched
        customer_id (e.g. a stale/forged token) is rejected too.
        """
        return (
            self.db.query(Conversation)
            .filter(
                Conversation.id == conversation_id,
                Conversation.owner_id == owner_id,
                Conversation.customer_id == customer_id
            )
            .first()
        )

    def get_all_for_owner(self, owner_id: int, customer_id: int):
        """
        Sidebar listing — most recently updated conversation first, same
        ordering convention ChatGPT uses (last activity, not creation time).
        Scoped by both owner_id and customer_id.
        """
        return (
            self.db.query(Conversation)
            .filter(
                Conversation.owner_id == owner_id,
                Conversation.customer_id == customer_id
            )
            .order_by(Conversation.updated_at.desc())
            .all()
        )

    def update_title(self, conversation: Conversation, title: str):
        conversation.title = title
        self.db.commit()
        self.db.refresh(conversation)
        return conversation

    def touch(self, conversation: Conversation):
        """
        Bumps updated_at (via onupdate) so this conversation floats back to
        the top of the sidebar list after a new message is sent to it.
        SQLAlchemy's onupdate only fires on an actual column change, so we
        nudge it by re-assigning title to itself.
        """
        conversation.title = conversation.title
        self.db.commit()
        self.db.refresh(conversation)
        return conversation

    def delete(self, conversation: Conversation):
        self.db.delete(conversation)
        self.db.commit()

    def delete_all_for_customer(self, customer_id: int):
        """
        Hard-deletes every conversation for a tenant — used when a
        Customer itself is deleted (see CustomerService.delete). A plain
        bulk query.delete() doesn't run the ORM-level
        cascade="all, delete-orphan" on Conversation.messages, but the
        chat_messages.conversation_id FK already has ON DELETE CASCADE
        at the database level (see the 85fbde5d9615 migration), so
        Postgres removes the messages for us regardless of how the
        Conversation rows themselves are deleted.
        """
        (
            self.db.query(Conversation)
            .filter(Conversation.customer_id == customer_id)
            .delete(synchronize_session=False)
        )
        self.db.commit()
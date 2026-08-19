from typing import List

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException

from sqlalchemy.orm import Session

from app.database.session import get_db
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.chat_message_repository import ChatMessageRepository
from app.models.conversation import Conversation
from app.schemas.conversation import (
    ConversationSummary,
    ConversationDetail,
    ConversationCreate
)
from app.api.dependencies.auth import get_customer_user

router = APIRouter(
    prefix="/conversations",
    tags=["Conversations"]
)


# ─────────────────────────────────────────
# List Sidebar Conversations (current user only)
# ─────────────────────────────────────────
@router.get("", response_model=List[ConversationSummary], status_code=200)
def list_conversations(
    db: Session = Depends(get_db),
    current_user=Depends(get_customer_user)
):
    repo = ConversationRepository(db)
    return repo.get_all_for_owner(current_user.id, current_user.customer_id)


# ─────────────────────────────────────────
# Create a New (Empty) Conversation
# ─────────────────────────────────────────
@router.post("", response_model=ConversationSummary, status_code=201)
def create_conversation(
    payload: ConversationCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_customer_user)
):
    repo = ConversationRepository(db)

    conversation = Conversation(
        title=payload.title or "New Chat",
        owner_id=current_user.id,
        customer_id=current_user.customer_id
    )

    return repo.create(conversation)


# ─────────────────────────────────────────
# Get One Conversation + Its Full Message History
# ─────────────────────────────────────────
@router.get("/{conversation_id}", response_model=ConversationDetail, status_code=200)
def get_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_customer_user)
):
    conv_repo = ConversationRepository(db)
    msg_repo = ChatMessageRepository(db)

    conversation = conv_repo.get_by_id_for_owner(
        conversation_id, current_user.id, current_user.customer_id
    )

    if not conversation:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found"
        )

    messages = msg_repo.get_all_for_conversation(conversation_id)

    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        messages=messages
    )


# ─────────────────────────────────────────
# Delete a Conversation (and its messages, via cascade)
# ─────────────────────────────────────────
@router.delete("/{conversation_id}", status_code=200)
def delete_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_customer_user)
):
    conv_repo = ConversationRepository(db)

    conversation = conv_repo.get_by_id_for_owner(
        conversation_id, current_user.id, current_user.customer_id
    )

    if not conversation:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found"
        )

    conv_repo.delete(conversation)

    return {
        "message": "Conversation deleted successfully"
    }
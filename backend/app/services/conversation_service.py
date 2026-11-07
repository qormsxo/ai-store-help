import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ConversationClosedError, ConversationNotFoundError
from app.models import Conversation, ConversationStatus, Message, MessageRole
from app.repositories import conversation_repository

# Agent에 넘기는 고객 대화. system 메시지는 고객과 주고받은 내용이 아니므로 제외한다.
_DIALOGUE_ROLES = (MessageRole.USER, MessageRole.ASSISTANT)


async def get_open_conversation(session: AsyncSession, store_id: uuid.UUID, conversation_id: uuid.UUID) -> Conversation:
    conversation = await conversation_repository.find_conversation_in_store(session, store_id, conversation_id)
    if conversation is None:
        raise ConversationNotFoundError("대화를 찾을 수 없습니다.")
    if conversation.status is ConversationStatus.CLOSED:
        raise ConversationClosedError("종료된 대화입니다.")
    return conversation


async def get_recent_dialogue(session: AsyncSession, conversation: Conversation, limit: int) -> list[Message]:
    return await conversation_repository.list_recent_messages(session, conversation.id, _DIALOGUE_ROLES, limit)


async def add_message(session: AsyncSession, conversation: Conversation, role: MessageRole, content: str) -> Message:
    message = Message(conversation_id=conversation.id, role=role, content=content)
    session.add(message)
    await session.flush()
    return message

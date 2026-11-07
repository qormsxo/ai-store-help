import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Message, MessageRole


async def find_conversation_in_store(
    session: AsyncSession, store_id: uuid.UUID, conversation_id: uuid.UUID
) -> Conversation | None:
    """다른 매장의 대화는 id가 맞아도 찾지 않는다."""
    return await session.scalar(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.store_id == store_id)
    )


async def list_recent_messages(
    session: AsyncSession, conversation_id: uuid.UUID, roles: tuple[MessageRole, ...], limit: int
) -> list[Message]:
    """대화의 최근 메시지 limit개를 오래된 것부터 반환한다."""
    newest_first = await session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.role.in_(roles))
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
    )
    return list(reversed(newest_first.all()))

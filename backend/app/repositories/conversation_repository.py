import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Message, MessageRole


async def find_conversation_in_store(
    session: AsyncSession, store_id: uuid.UUID, conversation_id: uuid.UUID
) -> Conversation | None:
    """다른 매장의 대화는 id가 맞아도 찾지 않는다."""
    return await session.scalar(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.store_id == store_id)
    )


async def list_conversations_for_visitor(
    session: AsyncSession, store_id: uuid.UUID, visitor_id: str, roles: tuple[MessageRole, ...]
) -> list[tuple[Conversation, str | None]]:
    """현재 매장에서 그 손님의 대화를 최근 활동순으로 돌려준다. 두 번째 값은 마지막 대화 메시지다."""
    last_message = (
        select(Message.content)
        .where(Message.conversation_id == Conversation.id, Message.role.in_(roles))
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
        .correlate(Conversation)
        .scalar_subquery()
    )
    last_activity = (
        select(func.max(Message.created_at))
        .where(Message.conversation_id == Conversation.id, Message.role.in_(roles))
        .correlate(Conversation)
        .scalar_subquery()
    )
    result = await session.execute(
        select(Conversation, last_message)
        .where(Conversation.store_id == store_id, Conversation.visitor_id == visitor_id)
        .order_by(func.coalesce(last_activity, Conversation.created_at).desc(), Conversation.id.desc())
    )
    return [(conversation, preview) for conversation, preview in result.all()]


async def list_messages(
    session: AsyncSession, conversation_id: uuid.UUID, roles: tuple[MessageRole, ...]
) -> list[Message]:
    """대화의 메시지를 오래된 것부터 반환한다."""
    result = await session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.role.in_(roles))
        .order_by(Message.created_at, Message.id)
    )
    return list(result.all())


async def list_recent_messages(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    roles: tuple[MessageRole, ...],
    limit: int,
    exclude_message_id: uuid.UUID | None = None,
) -> list[Message]:
    """대화의 최근 메시지 limit개를 오래된 것부터 반환한다. exclude_message_id는 결과에서 뺀다."""
    statement = select(Message).where(Message.conversation_id == conversation_id, Message.role.in_(roles))
    if exclude_message_id is not None:
        statement = statement.where(Message.id != exclude_message_id)
    newest_first = await session.scalars(statement.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit))
    return list(reversed(newest_first.all()))

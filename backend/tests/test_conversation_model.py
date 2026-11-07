from datetime import timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Conversation, ConversationStatus, Message, SupportTicket
from tests.factories import create_conversation, create_message, create_ticket


async def test_create_and_get_conversation(session: AsyncSession) -> None:
    created = await create_conversation(session, visitor_id="visitor-abc")
    session.expunge_all()

    found = await session.scalar(select(Conversation).where(Conversation.id == created.id))

    assert found is not None
    assert found.visitor_id == "visitor-abc"


async def test_conversation_defaults(session: AsyncSession) -> None:
    created = await create_conversation(session)
    session.expunge_all()

    found = await session.get_one(Conversation, created.id)

    assert found.status == ConversationStatus.ACTIVE
    assert found.created_at.utcoffset() == timedelta(0)
    assert found.updated_at.utcoffset() == timedelta(0)


async def test_updated_at_changes_on_update(session: AsyncSession) -> None:
    conversation = await create_conversation(session)
    created_at_before = conversation.created_at
    updated_at_before = conversation.updated_at

    conversation.status = ConversationStatus.CLOSED
    await session.flush()

    assert conversation.updated_at > updated_at_before
    assert conversation.created_at == created_at_before


async def test_list_conversations_by_visitor(session: AsyncSession) -> None:
    await create_conversation(session, visitor_id="visitor-a")
    await create_conversation(session, visitor_id="visitor-a")
    await create_conversation(session, visitor_id="visitor-b")

    found = (await session.scalars(select(Conversation).where(Conversation.visitor_id == "visitor-a"))).all()

    assert len(found) == 2


async def test_get_missing_conversation_returns_none(session: AsyncSession) -> None:
    other = await create_conversation(session)
    await session.execute(delete(Conversation).where(Conversation.id == other.id))

    assert await session.get(Conversation, other.id) is None


async def test_messages_and_tickets_load_explicitly(session: AsyncSession) -> None:
    conversation = await create_conversation(session)
    first = await create_message(session, conversation, content="첫 번째")
    await create_message(session, conversation, content="두 번째")
    await create_ticket(session, conversation, source_message=first)
    session.expunge_all()

    loaded = await session.scalar(
        select(Conversation)
        .where(Conversation.id == conversation.id)
        .options(selectinload(Conversation.messages), selectinload(Conversation.tickets))
    )

    assert loaded is not None
    assert [m.content for m in loaded.messages] == ["첫 번째", "두 번째"]
    assert len(loaded.tickets) == 1


async def test_implicit_lazy_load_is_blocked(session: AsyncSession) -> None:
    conversation = await create_conversation(session)
    session.expunge_all()
    loaded = await session.get_one(Conversation, conversation.id)

    with pytest.raises(InvalidRequestError):
        _ = loaded.messages


async def test_deleting_conversation_cascades_to_messages_and_tickets(session: AsyncSession) -> None:
    conversation = await create_conversation(session)
    message = await create_message(session, conversation)
    await create_ticket(session, conversation, source_message=message)

    await session.execute(delete(Conversation).where(Conversation.id == conversation.id))

    message_count = await session.scalar(select(func.count()).select_from(Message))
    ticket_count = await session.scalar(select(func.count()).select_from(SupportTicket))
    assert message_count == 0
    assert ticket_count == 0

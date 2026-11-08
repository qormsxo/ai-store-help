import uuid
from datetime import timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Conversation, Message, Store, SupportTicket
from tests.factories import create_conversation, create_message, create_store, create_ticket


async def test_create_and_get_conversation(session: AsyncSession, store: Store) -> None:
    created = await create_conversation(session, store, visitor_id="visitor-abc")
    session.expunge_all()

    found = await session.scalar(select(Conversation).where(Conversation.id == created.id))

    assert found is not None
    assert found.visitor_id == "visitor-abc"


async def test_conversation_defaults(session: AsyncSession, store: Store) -> None:
    created = await create_conversation(session, store)
    session.expunge_all()

    found = await session.get_one(Conversation, created.id)

    assert found.created_at.utcoffset() == timedelta(0)
    assert found.updated_at.utcoffset() == timedelta(0)


async def test_updated_at_changes_on_update(session: AsyncSession, store: Store) -> None:
    conversation = await create_conversation(session, store)
    created_at_before = conversation.created_at
    updated_at_before = conversation.updated_at

    conversation.visitor_id = "visitor-updated"
    await session.flush()

    assert conversation.updated_at > updated_at_before
    assert conversation.created_at == created_at_before


async def test_messages_and_tickets_load_explicitly(session: AsyncSession, store: Store) -> None:
    conversation = await create_conversation(session, store)
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


async def test_implicit_lazy_load_is_blocked(session: AsyncSession, store: Store) -> None:
    conversation = await create_conversation(session, store)
    session.expunge_all()
    loaded = await session.get_one(Conversation, conversation.id)

    with pytest.raises(InvalidRequestError):
        _ = loaded.messages


async def test_deleting_conversation_cascades_to_messages_and_tickets(session: AsyncSession, store: Store) -> None:
    conversation = await create_conversation(session, store)
    message = await create_message(session, conversation)
    await create_ticket(session, conversation, source_message=message)

    await session.execute(delete(Conversation).where(Conversation.id == conversation.id))

    message_count = await session.scalar(select(func.count()).select_from(Message))
    ticket_count = await session.scalar(select(func.count()).select_from(SupportTicket))
    assert message_count == 0
    assert ticket_count == 0


async def test_conversation_requires_existing_store(session: AsyncSession) -> None:
    session.add(Conversation(store_id=uuid.uuid4(), visitor_id="visitor-1"))

    with pytest.raises(IntegrityError, match="fk_conversations_store_id_stores"):
        await session.flush()


async def test_same_visitor_can_start_conversations_in_different_stores(
    session: AsyncSession, store: Store
) -> None:
    other = await create_store(session, slug="other-store")
    await create_conversation(session, store, visitor_id="visitor-a")
    await create_conversation(session, other, visitor_id="visitor-a")

    found = (
        await session.scalars(
            select(Conversation).where(Conversation.store_id == store.id, Conversation.visitor_id == "visitor-a")
        )
    ).all()

    assert len(found) == 1

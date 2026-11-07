import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Message, MessageRole, Store
from tests.factories import create_conversation, create_message


async def test_create_and_get_message(session: AsyncSession, store: Store) -> None:
    conversation = await create_conversation(session, store)
    created = await create_message(session, conversation, content="주차 가능한가요?", role=MessageRole.USER)
    session.expunge_all()

    found = await session.get_one(Message, created.id)

    assert found.conversation_id == conversation.id
    assert found.role == MessageRole.USER
    assert found.content == "주차 가능한가요?"
    assert found.created_at.utcoffset() == timedelta(0)


async def test_role_is_stored_as_lowercase_value(session: AsyncSession, store: Store) -> None:
    conversation = await create_conversation(session, store)
    message = await create_message(session, conversation, role=MessageRole.ASSISTANT)

    stored = await session.scalar(text("SELECT role::text FROM messages WHERE id = :id"), {"id": message.id})

    assert stored == "assistant"


async def test_messages_are_listed_in_creation_order(session: AsyncSession, store: Store) -> None:
    conversation = await create_conversation(session, store)
    other = await create_conversation(session, store, visitor_id="other")
    await create_message(session, conversation, content="질문", role=MessageRole.USER)
    await create_message(session, conversation, content="답변", role=MessageRole.ASSISTANT)
    await create_message(session, other, content="다른 대화")

    found = (
        await session.scalars(
            select(Message).where(Message.conversation_id == conversation.id).order_by(Message.created_at)
        )
    ).all()

    assert [m.content for m in found] == ["질문", "답변"]


async def test_message_requires_existing_conversation(session: AsyncSession) -> None:
    session.add(Message(conversation_id=uuid.uuid4(), role=MessageRole.USER, content="고아 메시지"))

    with pytest.raises(IntegrityError, match="fk_messages_conversation_id_conversations"):
        await session.flush()


async def test_invalid_role_is_rejected_by_database(session: AsyncSession, store: Store) -> None:
    conversation = await create_conversation(session, store)

    with pytest.raises(DBAPIError, match="message_role"):
        await session.execute(
            text("INSERT INTO messages (conversation_id, role, content) VALUES (:cid, 'robot', 'x')"),
            {"cid": conversation.id},
        )

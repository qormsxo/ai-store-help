import uuid
from datetime import timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import EscalationReason, Message, Store, SupportTicket, TicketStatus, utcnow
from tests.factories import create_conversation, create_knowledge, create_message, create_store, create_ticket


async def test_create_and_get_ticket(session: AsyncSession, store: Store) -> None:
    conversation = await create_conversation(session, store)
    message = await create_message(session, conversation)
    created = await create_ticket(session, conversation, source_message=message, question="주차 가능 여부")
    session.expunge_all()

    found = await session.get_one(SupportTicket, created.id)

    assert found.conversation_id == conversation.id
    assert found.source_message_id == message.id
    assert found.question == "주차 가능 여부"
    assert found.escalation_reason == EscalationReason.NO_KNOWLEDGE


async def test_ticket_defaults_to_open(session: AsyncSession, store: Store) -> None:
    conversation = await create_conversation(session, store)
    created = await create_ticket(session, conversation)
    session.expunge_all()

    found = await session.get_one(SupportTicket, created.id)

    assert found.status == TicketStatus.OPEN
    assert found.owner_answer is None
    assert found.resolved_at is None
    assert found.created_at.utcoffset() == timedelta(0)


async def test_list_open_tickets_oldest_first(session: AsyncSession, store: Store) -> None:
    conversation = await create_conversation(session, store)
    first = await create_ticket(session, conversation, question="첫 문의")
    second = await create_ticket(session, conversation, question="둘째 문의")
    done = await create_ticket(session, conversation, question="처리된 문의")
    done.owner_answer = "답변"
    done.status = TicketStatus.RESOLVED
    done.resolved_at = utcnow()
    await session.flush()

    found = (
        await session.scalars(
            select(SupportTicket)
            .where(SupportTicket.status == TicketStatus.OPEN)
            .order_by(SupportTicket.created_at)
        )
    ).all()

    assert [t.id for t in found] == [first.id, second.id]


async def test_resolve_ticket_persists_answer(session: AsyncSession, store: Store) -> None:
    conversation = await create_conversation(session, store)
    ticket = await create_ticket(session, conversation)

    ticket.owner_answer = "주차는 건물 지하 1층에 가능합니다."
    ticket.status = TicketStatus.RESOLVED
    ticket.resolved_at = utcnow()
    await session.flush()
    session.expunge_all()

    found = await session.get_one(SupportTicket, ticket.id)
    assert found.status == TicketStatus.RESOLVED
    assert found.owner_answer == "주차는 건물 지하 1층에 가능합니다."
    assert found.resolved_at is not None
    assert found.resolved_at.utcoffset() == timedelta(0)


async def test_resolved_ticket_requires_answer_and_time(session: AsyncSession, store: Store) -> None:
    conversation = await create_conversation(session, store)
    ticket = await create_ticket(session, conversation)

    ticket.status = TicketStatus.RESOLVED

    with pytest.raises(IntegrityError, match="ck_support_tickets_resolved_requires_answer"):
        await session.flush()


async def test_ticket_requires_existing_conversation(session: AsyncSession, store: Store) -> None:
    session.add(
        SupportTicket(
            store_id=store.id,
            conversation_id=uuid.uuid4(),
            question="고아 티켓",
            escalation_reason=EscalationReason.NO_KNOWLEDGE,
        )
    )

    with pytest.raises(IntegrityError, match="fk_support_tickets_conversation_id_conversations"):
        await session.flush()


async def test_one_message_cannot_create_two_tickets(session: AsyncSession, store: Store) -> None:
    conversation = await create_conversation(session, store)
    message = await create_message(session, conversation)
    await create_ticket(session, conversation, source_message=message)

    with pytest.raises(IntegrityError, match="uq_support_tickets_source_message_id"):
        await create_ticket(session, conversation, source_message=message)


async def test_tickets_without_source_message_can_coexist(session: AsyncSession, store: Store) -> None:
    conversation = await create_conversation(session, store)

    await create_ticket(session, conversation, source_message=None)
    await create_ticket(session, conversation, source_message=None)

    found = (await session.scalars(select(SupportTicket))).all()
    assert len(found) == 2


async def test_deleting_source_message_keeps_ticket(session: AsyncSession, store: Store) -> None:
    conversation = await create_conversation(session, store)
    message = await create_message(session, conversation)
    ticket = await create_ticket(session, conversation, source_message=message)

    await session.execute(delete(Message).where(Message.id == message.id))
    session.expunge_all()

    found = await session.get_one(SupportTicket, ticket.id)
    assert found.source_message_id is None


async def test_ticket_relationships_load_explicitly(session: AsyncSession, store: Store) -> None:
    conversation = await create_conversation(session, store)
    message = await create_message(session, conversation)
    ticket = await create_ticket(session, conversation, source_message=message)
    session.expunge_all()

    loaded = await session.scalar(
        select(SupportTicket)
        .where(SupportTicket.id == ticket.id)
        .options(
            selectinload(SupportTicket.conversation),
            selectinload(SupportTicket.source_message),
            selectinload(SupportTicket.knowledge_entry),
        )
    )

    assert loaded is not None
    assert loaded.conversation.id == conversation.id
    assert loaded.source_message is not None
    assert loaded.source_message.id == message.id
    assert loaded.knowledge_entry is None


async def test_ticket_links_to_its_knowledge_entry(session: AsyncSession, store: Store) -> None:
    conversation = await create_conversation(session, store)
    ticket = await create_ticket(session, conversation)
    entry = await create_knowledge(session, store, ticket=ticket)
    session.expunge_all()

    loaded = await session.scalar(
        select(SupportTicket)
        .where(SupportTicket.id == ticket.id)
        .options(selectinload(SupportTicket.knowledge_entry))
    )

    assert loaded is not None
    assert loaded.knowledge_entry is not None
    assert loaded.knowledge_entry.id == entry.id


async def test_ticket_requires_existing_store(session: AsyncSession, store: Store) -> None:
    conversation = await create_conversation(session, store)
    session.add(
        SupportTicket(
            store_id=uuid.uuid4(),
            conversation_id=conversation.id,
            question="매장 없는 티켓",
            escalation_reason=EscalationReason.NO_KNOWLEDGE,
        )
    )

    with pytest.raises(IntegrityError, match="fk_support_tickets_store_id_stores"):
        await session.flush()


async def test_open_tickets_are_scoped_to_their_store(session: AsyncSession, store: Store) -> None:
    other = await create_store(session, slug="other-store")
    mine = await create_ticket(session, await create_conversation(session, store))
    await create_ticket(session, await create_conversation(session, other))

    found = (
        await session.scalars(
            select(SupportTicket).where(SupportTicket.store_id == store.id, SupportTicket.status == TicketStatus.OPEN)
        )
    ).all()

    assert [t.id for t in found] == [mine.id]

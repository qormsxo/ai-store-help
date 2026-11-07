from datetime import timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import KnowledgeEntry, SupportTicket
from tests.factories import create_conversation, create_knowledge, create_ticket


async def test_empty_knowledge_table_is_queryable(session: AsyncSession) -> None:
    count = await session.scalar(select(func.count()).select_from(KnowledgeEntry))
    entries = (await session.scalars(select(KnowledgeEntry).where(KnowledgeEntry.is_active))).all()

    assert count == 0
    assert entries == []


async def test_create_and_get_knowledge_entry(session: AsyncSession) -> None:
    created = await create_knowledge(session, question="주차 가능한가요?", answer="지하 1층 이용 가능합니다.")
    session.expunge_all()

    found = await session.get_one(KnowledgeEntry, created.id)

    assert found.question == "주차 가능한가요?"
    assert found.answer == "지하 1층 이용 가능합니다."
    assert found.source_ticket_id is None


async def test_knowledge_defaults(session: AsyncSession) -> None:
    created = await create_knowledge(session)
    session.expunge_all()

    found = await session.get_one(KnowledgeEntry, created.id)

    assert found.is_active is True
    assert found.created_at.utcoffset() == timedelta(0)
    assert found.updated_at.utcoffset() == timedelta(0)


async def test_updated_at_changes_on_update(session: AsyncSession) -> None:
    entry = await create_knowledge(session)
    before = entry.updated_at

    entry.answer = "수정된 답변"
    await session.flush()

    assert entry.updated_at > before


async def test_entry_created_from_ticket_keeps_source(session: AsyncSession) -> None:
    conversation = await create_conversation(session)
    ticket = await create_ticket(session, conversation)
    created = await create_knowledge(session, ticket=ticket)
    session.expunge_all()

    found = await session.scalar(
        select(KnowledgeEntry)
        .where(KnowledgeEntry.id == created.id)
        .options(selectinload(KnowledgeEntry.source_ticket))
    )

    assert found is not None
    assert found.source_ticket is not None
    assert found.source_ticket.id == ticket.id


async def test_same_active_question_is_rejected(session: AsyncSession) -> None:
    await create_knowledge(session, question="영업시간이 어떻게 되나요?")

    with pytest.raises(IntegrityError, match="uq_knowledge_entries_active_question"):
        await create_knowledge(session, question="영업시간이 어떻게 되나요?", answer="다른 답변")


async def test_duplicate_check_ignores_case_and_whitespace(session: AsyncSession) -> None:
    await create_knowledge(session, question="Do you take   reservations?")

    with pytest.raises(IntegrityError, match="uq_knowledge_entries_active_question"):
        await create_knowledge(session, question="  do you take reservations?  ")


async def test_same_question_allowed_after_deactivation(session: AsyncSession) -> None:
    await create_knowledge(session, question="영업시간이 어떻게 되나요?", is_active=False)

    await create_knowledge(session, question="영업시간이 어떻게 되나요?")

    count = await session.scalar(select(func.count()).select_from(KnowledgeEntry))
    assert count == 2


async def test_different_questions_can_coexist(session: AsyncSession) -> None:
    await create_knowledge(session, question="영업시간이 어떻게 되나요?")
    await create_knowledge(session, question="주차 가능한가요?")

    count = await session.scalar(select(func.count()).select_from(KnowledgeEntry))
    assert count == 2


async def test_one_ticket_cannot_create_two_entries(session: AsyncSession) -> None:
    conversation = await create_conversation(session)
    ticket = await create_ticket(session, conversation)
    await create_knowledge(session, question="질문 A", ticket=ticket)

    with pytest.raises(IntegrityError, match="uq_knowledge_entries_source_ticket_id"):
        await create_knowledge(session, question="질문 B", ticket=ticket)


async def test_entries_without_ticket_can_coexist(session: AsyncSession) -> None:
    await create_knowledge(session, question="질문 A")
    await create_knowledge(session, question="질문 B")

    count = await session.scalar(select(func.count()).select_from(KnowledgeEntry))
    assert count == 2


async def test_deleting_ticket_keeps_knowledge(session: AsyncSession) -> None:
    conversation = await create_conversation(session)
    ticket = await create_ticket(session, conversation)
    entry = await create_knowledge(session, ticket=ticket)

    await session.execute(delete(SupportTicket).where(SupportTicket.id == ticket.id))
    session.expunge_all()

    found = await session.get_one(KnowledgeEntry, entry.id)
    assert found.source_ticket_id is None


async def test_question_longer_than_limit_is_rejected(session: AsyncSession) -> None:
    with pytest.raises(DBAPIError, match="value too long"):
        await create_knowledge(session, question="가" * 501)

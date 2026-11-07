import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KnowledgeEntry, SupportTicket
from app.repositories import knowledge_repository


@dataclass(frozen=True)
class SavedKnowledge:
    entry: KnowledgeEntry
    # 같은 질문의 활성 지식이 있어서 대체한 경우 그 지식의 id
    superseded_entry_id: uuid.UUID | None


async def save_owner_answer(session: AsyncSession, ticket: SupportTicket, answer: str) -> SavedKnowledge:
    """티켓의 질문과 사장 답변을 지식으로 저장한다. flush만 하고 커밋은 호출하는 쪽이 한다.

    같은 매장에 같은 질문(공백, 대소문자 무시)의 활성 지식이 이미 있으면 그것을 비활성화하고 새 지식을 만든다.
    같은 질문을 한 고객이 둘이라 티켓이 둘인 경우에 두 번째 답변이 막히면 그 티켓은 영영 해결할 수 없다.
    가장 최근에 사장이 한 답이 현재 답이므로 새 답변이 이전 지식을 대신하고, 이전 지식은 기록으로 남는다.
    티켓 하나에서 지식이 둘 생기는 것은 source_ticket_id의 unique 제약이 막는다.
    """
    existing = await knowledge_repository.find_active_entry_by_question(session, ticket.store_id, ticket.question)
    superseded_entry_id = None
    if existing is not None:
        existing.is_active = False
        # 활성 질문 unique 인덱스에 걸리지 않도록 새 지식을 넣기 전에 먼저 반영한다.
        await session.flush()
        superseded_entry_id = existing.id

    entry = KnowledgeEntry(
        store_id=ticket.store_id,
        question=ticket.question,
        answer=answer,
        source_ticket_id=ticket.id,
    )
    session.add(entry)
    await session.flush()
    return SavedKnowledge(entry=entry, superseded_entry_id=superseded_entry_id)

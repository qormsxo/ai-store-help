import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KnowledgeEntry, KnowledgeLifetime, Store, SupportTicket, utcnow
from app.repositories import knowledge_repository


@dataclass(frozen=True)
class SavedKnowledge:
    entry: KnowledgeEntry
    # 같은 질문의 활성 지식이 있어서 대체한 경우 그 지식의 id
    superseded_entry_id: uuid.UUID | None


def end_of_local_day(moment: datetime, timezone_name: str) -> datetime:
    """그 시각이 속한 매장 지역의 다음 날 0시. 오늘만 쓰는 지식은 이 시각부터 빠진다."""
    zone = ZoneInfo(timezone_name)
    local = moment.astimezone(zone)
    return datetime.combine(local.date() + timedelta(days=1), time.min, tzinfo=zone)


async def save_owner_answer(
    session: AsyncSession, ticket: SupportTicket, answer: str, lifetime: KnowledgeLifetime
) -> SavedKnowledge | None:
    """사장이 고른 방식대로 답변을 지식으로 남긴다. flush만 하고 커밋은 호출하는 쪽이 한다.

    none이면 아무것도 저장하지 않는다. permanent는 기한 없이 남긴다.
    until_end_of_day는 매장 시간대 기준 다음 날 0시에 만료된다. 시각은 서버가 계산한다.
    같은 질문의 활성 지식이 있으면, 만료됐더라도 자리를 비우고 새 지식으로 바꾼다.
    """
    if lifetime is KnowledgeLifetime.NONE:
        return None

    existing = await knowledge_repository.find_active_entry_by_question(
        session, ticket.store_id, ticket.question, reusable_only=False
    )
    superseded_entry_id = None
    if existing is not None:
        existing.is_active = False
        # 활성 질문 unique 인덱스에 걸리지 않도록 새 지식을 넣기 전에 먼저 반영한다.
        await session.flush()
        superseded_entry_id = existing.id

    expires_at = None
    if lifetime is KnowledgeLifetime.UNTIL_END_OF_DAY:
        timezone_name = await session.scalar(select(Store.timezone).where(Store.id == ticket.store_id))
        # 티켓의 매장은 외래 키로 항상 있다. 없으면 기한을 UTC로 계산한다.
        expires_at = end_of_local_day(utcnow(), timezone_name if timezone_name is not None else "UTC")

    entry = KnowledgeEntry(
        store_id=ticket.store_id,
        question=ticket.question,
        answer=answer,
        source_ticket_id=ticket.id,
        expires_at=expires_at,
    )
    session.add(entry)
    await session.flush()
    return SavedKnowledge(entry=entry, superseded_entry_id=superseded_entry_id)

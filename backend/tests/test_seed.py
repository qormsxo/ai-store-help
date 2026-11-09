from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Base, Conversation, KnowledgeEntry, Store
from app.seed import PARKING_QUESTION, SEONGSU_FLOWER, seed_all, seed_store
from tests.factories import create_store

OAK_ANSWER = "매장 전용 주차장은 없습니다. 연남로 공영주차장을 이용해 주세요."
SLUG = "oak-pine-demo"


async def _count(session: AsyncSession, model: type[Base]) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


async def test_seed_does_not_overwrite_existing_store(session: AsyncSession) -> None:
    existing = await create_store(session, slug=SLUG, name="Customized Name")

    created = await seed_store(session, SLUG)
    session.expunge_all()

    store = await session.get_one(Store, existing.id)
    assert created is False
    assert store.name == "Customized Name"
    assert await _count(session, Store) == 1


async def test_seed_all_gives_each_store_its_own_parking_answer(session: AsyncSession) -> None:
    await seed_all(session)
    await seed_all(session)

    oak = await session.scalar(select(Store).where(Store.slug == SLUG).options(selectinload(Store.business_hours)))
    flower = await session.scalar(
        select(Store).where(Store.slug == SEONGSU_FLOWER.slug).options(selectinload(Store.business_hours))
    )
    answers = {
        row.store_id: row.answer
        for row in await session.scalars(select(KnowledgeEntry).where(KnowledgeEntry.question == PARKING_QUESTION))
    }

    assert oak is not None and flower is not None
    assert oak.address != flower.address
    assert {hour.day_of_week for hour in flower.business_hours if hour.is_closed} == {0}
    assert answers == {oak.id: OAK_ANSWER, flower.id: SEONGSU_FLOWER.parking_answer}
    assert await _count(session, KnowledgeEntry) == 2
    assert await _count(session, Conversation) == 0

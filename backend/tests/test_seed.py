from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Base, BusinessHour, Conversation, KnowledgeEntry, Message, Store, SupportTicket
from app.seed import PARKING_QUESTION, SEONGSU_FLOWER, seed_all, seed_store

OAK_ANSWER = "매장 전용 주차장은 없습니다. 연남로 공영주차장을 이용해 주세요."
from tests.factories import create_store

SLUG = "oak-pine-demo"


async def _count(session: AsyncSession, model: type[Base]) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


async def test_seed_creates_store_with_seven_business_hours(session: AsyncSession) -> None:
    created = await seed_store(session, SLUG)

    store = await session.scalar(
        select(Store).where(Store.slug == SLUG).options(selectinload(Store.business_hours))
    )
    assert created is True
    assert store is not None
    assert [h.day_of_week for h in store.business_hours] == [0, 1, 2, 3, 4, 5, 6]


async def test_seeded_store_has_complete_profile(session: AsyncSession) -> None:
    await seed_store(session, SLUG)

    store = await session.scalar(select(Store).where(Store.slug == SLUG))

    assert store is not None
    assert store.name
    assert store.address
    assert store.phone
    assert store.email
    assert store.website
    ZoneInfo(store.timezone)


async def test_seeded_hours_include_a_closed_day(session: AsyncSession) -> None:
    await seed_store(session, SLUG)

    store = await session.scalar(
        select(Store).where(Store.slug == SLUG).options(selectinload(Store.business_hours))
    )

    assert store is not None
    by_day = {h.day_of_week: h for h in store.business_hours}
    assert by_day[0].is_closed is False
    assert by_day[6].is_closed is True
    assert by_day[6].open_time is None


async def test_seed_is_idempotent(session: AsyncSession) -> None:
    first = await seed_store(session, SLUG)
    second = await seed_store(session, SLUG)
    third = await seed_store(session, SLUG)

    assert (first, second, third) == (True, False, False)
    assert await _count(session, Store) == 1
    assert await _count(session, BusinessHour) == 7


async def test_seed_does_not_overwrite_existing_store(session: AsyncSession) -> None:
    existing = await create_store(session, slug=SLUG, name="Customized Name")

    created = await seed_store(session, SLUG)
    session.expunge_all()

    store = await session.get_one(Store, existing.id)
    assert created is False
    assert store.name == "Customized Name"
    assert await _count(session, Store) == 1
    # 이미 있는 매장에는 영업시간도 건드리지 않는다.
    assert await _count(session, BusinessHour) == 0


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


async def test_seed_creates_no_other_data(session: AsyncSession) -> None:
    await seed_store(session, SLUG)

    assert await _count(session, KnowledgeEntry) == 0
    assert await _count(session, Conversation) == 0
    assert await _count(session, Message) == 0
    assert await _count(session, SupportTicket) == 0

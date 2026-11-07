import uuid
from datetime import time

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BusinessHour, Store
from tests.factories import create_business_hour, create_store


async def test_create_and_get_business_hour(session: AsyncSession, store: Store) -> None:
    created = await create_business_hour(session, store, 0, time(7, 0), time(18, 30))
    session.expunge_all()

    found = await session.get_one(BusinessHour, created.id)

    assert found.store_id == store.id
    assert found.day_of_week == 0
    assert found.open_time == time(7, 0)
    assert found.close_time == time(18, 30)
    assert found.is_closed is False


async def test_week_of_seven_days_can_be_stored(session: AsyncSession, store: Store) -> None:
    for day in range(7):
        await create_business_hour(session, store, day)

    found = (await session.scalars(select(BusinessHour).where(BusinessHour.store_id == store.id))).all()

    assert sorted(h.day_of_week for h in found) == [0, 1, 2, 3, 4, 5, 6]


async def test_closed_day_has_no_times(session: AsyncSession, store: Store) -> None:
    created = await create_business_hour(session, store, 6, open_time=None, close_time=None)
    session.expunge_all()

    found = await session.get_one(BusinessHour, created.id)

    assert found.open_time is None
    assert found.close_time is None
    assert found.is_closed is True


async def test_overnight_hours_are_allowed(session: AsyncSession, store: Store) -> None:
    created = await create_business_hour(session, store, 4, time(18, 0), time(2, 0))
    session.expunge_all()

    found = await session.get_one(BusinessHour, created.id)

    assert found.open_time == time(18, 0)
    assert found.close_time == time(2, 0)


async def test_same_store_same_day_is_rejected(session: AsyncSession, store: Store) -> None:
    await create_business_hour(session, store, 1)

    with pytest.raises(IntegrityError, match="uq_business_hours_store_id_day_of_week"):
        await create_business_hour(session, store, 1)


async def test_different_stores_can_share_the_same_day(session: AsyncSession, store: Store) -> None:
    other = await create_store(session, slug="other-store")

    await create_business_hour(session, store, 1)
    await create_business_hour(session, other, 1)

    found = (await session.scalars(select(BusinessHour).where(BusinessHour.day_of_week == 1))).all()
    assert len(found) == 2


@pytest.mark.parametrize("bad_day", [-1, 7])
async def test_day_of_week_out_of_range_is_rejected(session: AsyncSession, store: Store, bad_day: int) -> None:
    with pytest.raises(IntegrityError, match="ck_business_hours_day_of_week_range"):
        await create_business_hour(session, store, bad_day)


async def test_open_without_close_is_rejected(session: AsyncSession, store: Store) -> None:
    with pytest.raises(IntegrityError, match="ck_business_hours_open_close_both_or_neither"):
        await create_business_hour(session, store, 2, open_time=time(9, 0), close_time=None)


async def test_close_without_open_is_rejected(session: AsyncSession, store: Store) -> None:
    with pytest.raises(IntegrityError, match="ck_business_hours_open_close_both_or_neither"):
        await create_business_hour(session, store, 2, open_time=None, close_time=time(18, 0))


async def test_business_hour_requires_existing_store(session: AsyncSession) -> None:
    session.add(BusinessHour(store_id=uuid.uuid4(), day_of_week=0, open_time=time(9, 0), close_time=time(18, 0)))

    with pytest.raises(IntegrityError, match="fk_business_hours_store_id_stores"):
        await session.flush()

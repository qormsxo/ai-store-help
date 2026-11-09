from datetime import time

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BusinessHour, Store
from tests.factories import create_business_hour


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

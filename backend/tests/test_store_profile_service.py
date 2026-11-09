from datetime import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import store_profile_service
from tests.factories import create_business_hour, create_store


async def test_profile_omits_fields_that_are_not_in_db(session: AsyncSession) -> None:
    store = await create_store(session, name="Bare Store")
    session.expunge_all()

    profile = await store_profile_service.get_store_profile(session, store.id)

    assert profile.store.content == "Name: Bare Store\nTime zone: America/Los_Angeles"


async def test_profile_returns_business_hours_with_source(session: AsyncSession) -> None:
    store = await create_store(session)
    tuesday = await create_business_hour(session, store, 1, time(9, 0), time(17, 30))
    monday = await create_business_hour(session, store, 0, time(8, 0), time(16, 0))
    sunday = await create_business_hour(session, store, 6, None, None)
    session.expunge_all()

    profile = await store_profile_service.get_store_profile(session, store.id)

    assert [(h.source_type, h.source_id, h.content) for h in profile.business_hours] == [
        ("business_hour", monday.id, "Monday: 08:00-16:00"),
        ("business_hour", tuesday.id, "Tuesday: 09:00-17:30"),
        ("business_hour", sunday.id, "Sunday: Closed"),
    ]


async def test_profile_does_not_invent_missing_days(session: AsyncSession) -> None:
    store = await create_store(session)
    session.expunge_all()

    profile = await store_profile_service.get_store_profile(session, store.id)

    assert profile.business_hours == []

import uuid
from datetime import time

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import StoreNotConfiguredError
from app.services import store_profile_service
from tests.factories import create_business_hour, create_store


async def test_profile_returns_store_info_with_source(session: AsyncSession) -> None:
    store = await create_store(session, name="Oak & Pine")
    store.description = "Neighborhood coffee shop."
    store.address = "1427 Oak Street, Portland, OR 97205"
    store.phone = "+1 (503) 555-0142"
    await session.flush()
    session.expunge_all()

    profile = await store_profile_service.get_store_profile(session, store.id)

    assert profile.store.source_type == "store"
    assert profile.store.source_id == store.id
    assert profile.store.content == (
        "Name: Oak & Pine\n"
        "Description: Neighborhood coffee shop.\n"
        "Address: 1427 Oak Street, Portland, OR 97205\n"
        "Phone: +1 (503) 555-0142\n"
        "Time zone: America/Los_Angeles"
    )


async def test_profile_omits_fields_that_are_not_in_db(session: AsyncSession) -> None:
    store = await create_store(session, name="Bare Store")
    session.expunge_all()

    profile = await store_profile_service.get_store_profile(session, store.id)

    # 값이 없는 설명, 주소, 전화번호, 이메일, 웹사이트는 항목 자체가 없어야 한다.
    assert profile.store.content == "Name: Bare Store\nTime zone: America/Los_Angeles"


async def test_profile_returns_business_hours_with_source(session: AsyncSession) -> None:
    store = await create_store(session)
    tuesday = await create_business_hour(session, store, 1, time(9, 0), time(17, 30))
    monday = await create_business_hour(session, store, 0, time(8, 0), time(16, 0))
    sunday = await create_business_hour(session, store, 6, None, None)
    session.expunge_all()

    profile = await store_profile_service.get_store_profile(session, store.id)

    # 입력 순서와 상관없이 요일 순이고, 휴무일도 DB 값 그대로 Closed로 나온다.
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


async def test_profile_excludes_other_store_hours(session: AsyncSession) -> None:
    store = await create_store(session, slug="store-a")
    other = await create_store(session, slug="store-b")
    await create_business_hour(session, store, 0)
    await create_business_hour(session, other, 1)
    await create_business_hour(session, other, 2)
    session.expunge_all()

    profile = await store_profile_service.get_store_profile(session, store.id)

    assert len(profile.business_hours) == 1
    assert profile.business_hours[0].content.startswith("Monday")


async def test_profile_raises_when_store_missing(session: AsyncSession) -> None:
    with pytest.raises(StoreNotConfiguredError) as exc_info:
        await store_profile_service.get_store_profile(session, uuid.uuid4())

    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "STORE_NOT_CONFIGURED"

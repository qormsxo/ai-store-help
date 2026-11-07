from datetime import time

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.errors import StoreNotConfiguredError
from app.services import store_service
from tests.factories import create_business_hour, create_store


async def test_get_store_by_slug_returns_store_with_hours(session: AsyncSession) -> None:
    store = await create_store(session, slug="found-store")
    await create_business_hour(session, store, 1, time(9, 0), time(17, 0))
    await create_business_hour(session, store, 0, time(8, 0), time(16, 0))
    session.expunge_all()

    found = await store_service.get_store_by_slug(session, "found-store")

    assert found.id == store.id
    # selectinload로 이미 로드되어 있어야 한다. 아니면 lazy="raise"로 에러가 난다.
    assert [h.day_of_week for h in found.business_hours] == [0, 1]


async def test_get_store_by_slug_raises_when_missing(session: AsyncSession) -> None:
    with pytest.raises(StoreNotConfiguredError) as exc_info:
        await store_service.get_store_by_slug(session, "does-not-exist")

    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "STORE_NOT_CONFIGURED"


async def test_get_default_store_uses_configured_slug(session: AsyncSession) -> None:
    configured = await create_store(session, slug=get_settings().default_store_slug, name="Default")
    await create_store(session, slug="another-store", name="Another")

    found = await store_service.get_default_store(session)

    assert found.id == configured.id


async def test_get_default_store_raises_when_not_seeded(session: AsyncSession) -> None:
    await create_store(session, slug="another-store")

    with pytest.raises(StoreNotConfiguredError):
        await store_service.get_default_store(session)

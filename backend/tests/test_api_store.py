from datetime import time

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.main import app
from tests.factories import create_business_hour, create_store


async def test_get_store_returns_profile_and_hours(client: httpx.AsyncClient, session: AsyncSession) -> None:
    app.dependency_overrides[get_session] = lambda: session
    store = await create_store(session, slug=get_settings().default_store_slug, name="Oak & Pine")
    await create_business_hour(session, store, 6, open_time=None, close_time=None)
    await create_business_hour(session, store, 0, time(7, 0), time(18, 0))

    res = await client.get("/api/store")

    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "Oak & Pine"
    assert body["slug"] == get_settings().default_store_slug
    assert body["timezone"] == "America/Los_Angeles"
    assert body["business_hours"] == [
        {"day_of_week": 0, "open_time": "07:00:00", "close_time": "18:00:00", "is_closed": False},
        {"day_of_week": 6, "open_time": None, "close_time": None, "is_closed": True},
    ]


async def test_get_store_does_not_expose_internal_id(client: httpx.AsyncClient, session: AsyncSession) -> None:
    app.dependency_overrides[get_session] = lambda: session
    await create_store(session, slug=get_settings().default_store_slug)

    res = await client.get("/api/store")

    assert "id" not in res.json()


async def test_get_store_returns_503_when_store_is_not_seeded(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    app.dependency_overrides[get_session] = lambda: session

    res = await client.get("/api/store")

    assert res.status_code == 503
    assert res.json() == {"error": {"code": "STORE_NOT_CONFIGURED", "message": "매장 정보를 찾을 수 없습니다."}}


async def test_client_cannot_choose_the_store(client: httpx.AsyncClient, session: AsyncSession) -> None:
    app.dependency_overrides[get_session] = lambda: session
    await create_store(session, slug=get_settings().default_store_slug, name="Default Store")
    other = await create_store(session, slug="other-store", name="Other Store")

    by_query = await client.get("/api/store", params={"store_id": str(other.id), "slug": "other-store"})
    by_header = await client.get("/api/store", headers={"X-Store-Id": str(other.id)})

    assert by_query.json()["name"] == "Default Store"
    assert by_header.json()["name"] == "Default Store"

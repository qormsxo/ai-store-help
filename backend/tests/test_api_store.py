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


async def test_path_slug_selects_that_store(client: httpx.AsyncClient, session: AsyncSession) -> None:
    app.dependency_overrides[get_session] = lambda: session
    await create_store(session, slug=get_settings().default_store_slug, name="Default Store")
    await create_store(session, slug="other-store", name="Other Store")

    other = await client.get("/api/stores/other-store/store")
    default = await client.get("/api/store")

    assert other.status_code == 200
    assert other.json()["name"] == "Other Store"
    assert default.json()["name"] == "Default Store"


async def test_unknown_store_slug_is_not_found(client: httpx.AsyncClient, session: AsyncSession) -> None:
    app.dependency_overrides[get_session] = lambda: session

    missing = await client.get("/api/stores/missing-store/store")
    invalid = await client.get("/api/stores/Not_A_Store/store")

    assert missing.status_code == 404
    assert invalid.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"


async def test_store_path_does_not_share_conversations(client: httpx.AsyncClient, session: AsyncSession) -> None:
    app.dependency_overrides[get_session] = lambda: session
    await create_store(session, slug=get_settings().default_store_slug, name="Default Store")
    await create_store(session, slug="other-store", name="Other Store")

    created = await client.post("/api/stores/other-store/conversations", json={"visitor_id": "visitor-1"})
    default_list = await client.get("/api/conversations", params={"visitor_id": "visitor-1"})
    other_list = await client.get("/api/stores/other-store/conversations", params={"visitor_id": "visitor-1"})

    assert created.status_code == 201
    assert default_list.json() == []
    assert [row["id"] for row in other_list.json()] == [created.json()["id"]]

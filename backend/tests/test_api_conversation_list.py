"""손님별 대화 목록 API. 목록은 그 매장, 그 손님만 최근 활동순으로 돌려준다."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models import MessageRole, Store
from tests.factories import create_conversation, create_message, create_store

_START = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
async def store(session: AsyncSession) -> Store:
    app.dependency_overrides[get_session] = lambda: session
    return await create_store(session, slug=get_settings().default_store_slug)


async def test_lists_only_that_visitors_conversations_newest_activity_first(
    session: AsyncSession, client: httpx.AsyncClient, store: Store
) -> None:
    older = await create_conversation(session, store, visitor_id="demo-emma-walsh")
    first = await create_message(session, older, "First question")
    newer = await create_conversation(session, store, visitor_id="demo-emma-walsh")
    second = await create_message(session, newer, "Second question")
    reply = await create_message(session, older, "Emma came back", role=MessageRole.ASSISTANT)
    await create_conversation(session, store, visitor_id="demo-noah-bennett")
    # PostgreSQL now()는 트랜잭션 안에서 같은 시각이라, 순서는 직접 지정한다.
    older.created_at = _START
    first.created_at = _START + timedelta(minutes=1)
    newer.created_at = _START + timedelta(minutes=2)
    second.created_at = _START + timedelta(minutes=3)
    reply.created_at = _START + timedelta(minutes=4)
    await session.flush()

    res = await client.get("/api/conversations", params={"visitor_id": "demo-emma-walsh"})

    assert res.status_code == 200
    body = res.json()
    assert [item["id"] for item in body] == [str(older.id), str(newer.id)]
    assert body[0]["last_message"] == "Emma came back"
    assert body[0]["status"] == "active"
    assert body[1]["last_message"] == "Second question"


async def test_other_store_conversation_is_not_listed(
    session: AsyncSession, client: httpx.AsyncClient, store: Store
) -> None:
    other = await create_store(session, slug="other-store")
    hidden = await create_conversation(session, other, visitor_id="demo-emma-walsh")
    await create_message(session, hidden, "Secret")

    res = await client.get("/api/conversations", params={"visitor_id": "demo-emma-walsh"})

    assert res.status_code == 200
    assert res.json() == []


async def test_preview_is_the_last_dialogue_not_a_system_message_and_is_shortened(
    session: AsyncSession, client: httpx.AsyncClient, store: Store
) -> None:
    conversation = await create_conversation(session, store, visitor_id="demo-priya-shah")
    hello = await create_message(session, conversation, "Hello")
    note = await create_message(session, conversation, "system note", role=MessageRole.SYSTEM)
    answer = await create_message(session, conversation, "가" * 90, role=MessageRole.ASSISTANT)
    hello.created_at = _START
    note.created_at = _START + timedelta(minutes=1)
    answer.created_at = _START + timedelta(minutes=2)
    await session.flush()

    res = await client.get("/api/conversations", params={"visitor_id": "demo-priya-shah"})

    assert res.status_code == 200
    preview = res.json()[0]["last_message"]
    assert preview == "가" * 79 + "…"
    assert "system note" not in preview


async def test_unknown_visitor_is_an_empty_list(client: httpx.AsyncClient, store: Store) -> None:
    res = await client.get("/api/conversations", params={"visitor_id": "nobody"})

    assert res.status_code == 200
    assert res.json() == []


@pytest.mark.parametrize("params", [{}, {"visitor_id": ""}, {"visitor_id": "x" * 65}])
async def test_bad_visitor_id_is_422(client: httpx.AsyncClient, store: Store, params: dict[str, str]) -> None:
    res = await client.get("/api/conversations", params=params)

    assert res.status_code == 422

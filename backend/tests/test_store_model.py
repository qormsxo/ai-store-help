from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import BusinessHour, Store
from tests.factories import (
    create_business_hour,
    create_conversation,
    create_knowledge,
    create_store,
    create_ticket,
)


async def test_create_and_get_store(session: AsyncSession) -> None:
    session.add(
        Store(
            slug="oak-pine-demo",
            name="Oak & Pine",
            description="Coffee shop",
            address="1 Main St",
            phone="+1 555 0100",
            email="hi@oakpine.example",
            website="https://oakpine.example",
            timezone="America/Los_Angeles",
        )
    )
    await session.flush()
    session.expunge_all()

    found = await session.scalar(select(Store).where(Store.slug == "oak-pine-demo"))

    assert found is not None
    assert found.name == "Oak & Pine"
    assert found.address == "1 Main St"
    assert found.timezone == "America/Los_Angeles"


async def test_store_optional_fields_default_to_null(session: AsyncSession, store: Store) -> None:
    session.expunge_all()

    found = await session.get_one(Store, store.id)

    assert found.description is None
    assert found.address is None
    assert found.phone is None
    assert found.email is None
    assert found.website is None
    assert found.created_at.utcoffset() == timedelta(0)


async def test_updated_at_changes_on_update(session: AsyncSession, store: Store) -> None:
    before = store.updated_at

    store.name = "Renamed Store"
    await session.flush()

    assert store.updated_at > before


async def test_duplicate_slug_is_rejected(session: AsyncSession, store: Store) -> None:
    with pytest.raises(IntegrityError, match="uq_stores_slug"):
        await create_store(session, slug=store.slug)


@pytest.mark.parametrize("bad_slug", ["Oak-Pine", "oak_pine", "-oak", "oak-", "oak--pine", "oak pine", ""])
async def test_invalid_slug_is_rejected(session: AsyncSession, bad_slug: str) -> None:
    with pytest.raises(IntegrityError, match="ck_stores_slug_format"):
        await create_store(session, slug=bad_slug)


def test_invalid_timezone_is_rejected() -> None:
    with pytest.raises(ValueError, match="IANA"):
        Store(slug="x", name="X", timezone="Mars/Olympus")


async def test_store_loads_business_hours_in_weekday_order(session: AsyncSession, store: Store) -> None:
    await create_business_hour(session, store, day_of_week=2)
    await create_business_hour(session, store, day_of_week=0)
    await create_business_hour(session, store, day_of_week=1)
    session.expunge_all()

    loaded = await session.scalar(
        select(Store).where(Store.id == store.id).options(selectinload(Store.business_hours))
    )

    assert loaded is not None
    assert [h.day_of_week for h in loaded.business_hours] == [0, 1, 2]


async def test_deleting_store_cascades_to_business_hours(session: AsyncSession, store: Store) -> None:
    await create_business_hour(session, store, day_of_week=0)

    await session.delete(store)
    await session.flush()

    remaining = (await session.scalars(select(BusinessHour))).all()
    assert remaining == []


async def test_store_with_conversation_cannot_be_deleted(session: AsyncSession, store: Store) -> None:
    await create_conversation(session, store)

    with pytest.raises(IntegrityError, match="fk_conversations_store_id_stores"):
        await session.delete(store)
        await session.flush()


async def test_store_with_knowledge_cannot_be_deleted(session: AsyncSession, store: Store) -> None:
    await create_knowledge(session, store)

    with pytest.raises(IntegrityError, match="fk_knowledge_entries_store_id_stores"):
        await session.delete(store)
        await session.flush()


async def test_store_children_load_explicitly(session: AsyncSession, store: Store) -> None:
    conversation = await create_conversation(session, store)
    await create_ticket(session, conversation)
    await create_knowledge(session, store)
    session.expunge_all()

    loaded = await session.scalar(
        select(Store)
        .where(Store.id == store.id)
        .options(
            selectinload(Store.conversations),
            selectinload(Store.tickets),
            selectinload(Store.knowledge_entries),
        )
    )

    assert loaded is not None
    assert len(loaded.conversations) == 1
    assert len(loaded.tickets) == 1
    assert len(loaded.knowledge_entries) == 1


async def test_name_is_required(session: AsyncSession) -> None:
    session.add(Store(slug="no-name", timezone="UTC"))

    with pytest.raises(DBAPIError, match="name"):
        await session.flush()

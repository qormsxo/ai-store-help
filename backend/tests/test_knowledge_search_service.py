from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Store
from app.services import knowledge_search_service
from tests.factories import create_knowledge, create_store


async def test_expired_knowledge_is_not_searched(session: AsyncSession, store: Store) -> None:
    await create_knowledge(
        session,
        store,
        "지금 자리 있나요?",
        "네, 창가 두 자리 있습니다.",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )

    results = await knowledge_search_service.search_knowledge(session, store.id, "자리")

    assert results == []


async def test_partial_keywords_still_match(session: AsyncSession, store: Store) -> None:
    entry = await create_knowledge(
        session,
        store,
        "Are dogs allowed in the store?",
        "Yes, you are welcome to visit our store with your dog.",
    )

    results = await knowledge_search_service.search_knowledge(session, store.id, "dogs pets allowed inside")

    assert [r.source_id for r in results] == [entry.id]


async def test_keyword_matches_inside_a_longer_word(session: AsyncSession, store: Store) -> None:
    entry = await create_knowledge(
        session,
        store,
        "강아지와 함께 방문할 수 있나요?",
        "네, 저희 매장은 반려견과 함께 방문하실 수 있습니다.",
    )

    results = await knowledge_search_service.search_knowledge(session, store.id, "반려견 입장")

    assert [r.source_id for r in results] == [entry.id]


async def test_searches_answer_text_too(session: AsyncSession, store: Store) -> None:
    entry = await create_knowledge(
        session, store, "Where can I leave my car?", "Free parking is available in the back lot."
    )

    results = await knowledge_search_service.search_knowledge(session, store.id, "parking")

    assert [r.source_id for r in results] == [entry.id]


async def test_inactive_knowledge_is_excluded(session: AsyncSession, store: Store) -> None:
    await create_knowledge(session, store, "Is there parking?", "Yes, behind the shop.", is_active=False)

    results = await knowledge_search_service.search_knowledge(session, store.id, "parking")

    assert results == []


async def test_results_are_ordered_by_relevance(session: AsyncSession, store: Store) -> None:
    weak = await create_knowledge(
        session, store, "Do you sell gift cards?", "Yes. Cards can be used to validate parking next door."
    )
    strong = await create_knowledge(session, store, "Is parking available?", "Free parking in the back lot.")

    results = await knowledge_search_service.search_knowledge(session, store.id, "parking")

    assert [r.source_id for r in results] == [strong.id, weak.id]
    assert results[0].relevance_score > results[1].relevance_score > 0


async def test_other_store_knowledge_is_not_searched(session: AsyncSession, store: Store) -> None:
    other = await create_store(session, slug="other-store")
    mine = await create_knowledge(session, store, "Is parking available?", "Free lot behind our shop.")
    await create_knowledge(session, other, "Is parking available?", "Use the garage across the street.")

    results = await knowledge_search_service.search_knowledge(session, store.id, "parking")

    assert [r.source_id for r in results] == [mine.id]

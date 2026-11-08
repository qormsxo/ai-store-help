from datetime import UTC, datetime, timedelta

import pytest
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


async def test_empty_knowledge_returns_empty_list(session: AsyncSession, store: Store) -> None:
    results = await knowledge_search_service.search_knowledge(session, store.id, "parking")

    assert results == []


async def test_exact_question_match(session: AsyncSession, store: Store) -> None:
    entry = await create_knowledge(
        session, store, "What is your refund policy?", "Full refund within 7 days with a receipt."
    )

    results = await knowledge_search_service.search_knowledge(session, store.id, "What is your refund policy?")

    assert [r.source_id for r in results] == [entry.id]


@pytest.mark.parametrize("query", ["dog-friendly pets inside", "dogs pets allowed inside"])
async def test_partial_keywords_still_match(session: AsyncSession, store: Store, query: str) -> None:
    entry = await create_knowledge(
        session,
        store,
        "Are dogs allowed in the store?",
        "Yes, you are welcome to visit our store with your dog.",
    )

    results = await knowledge_search_service.search_knowledge(session, store.id, query)

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


async def test_korean_keywords_match(session: AsyncSession, store: Store) -> None:
    entry = await create_knowledge(
        session, store, "매장 안에 강아지 출입이 되나요?", "네, 강아지와 함께 오셔도 됩니다."
    )

    results = await knowledge_search_service.search_knowledge(session, store.id, "강아지 출입")

    assert [r.source_id for r in results] == [entry.id]


async def test_searches_answer_text_too(session: AsyncSession, store: Store) -> None:
    entry = await create_knowledge(
        session, store, "Where can I leave my car?", "Free parking is available in the back lot."
    )

    # "parking"은 질문에 없고 답변에만 있다.
    results = await knowledge_search_service.search_knowledge(session, store.id, "parking")

    assert [r.source_id for r in results] == [entry.id]


async def test_inactive_knowledge_is_excluded(session: AsyncSession, store: Store) -> None:
    await create_knowledge(session, store, "Is there parking?", "Yes, behind the shop.", is_active=False)

    results = await knowledge_search_service.search_knowledge(session, store.id, "parking")

    assert results == []


async def test_unrelated_question_returns_empty_list(session: AsyncSession, store: Store) -> None:
    await create_knowledge(session, store, "Is there parking?", "Yes, behind the shop.")

    results = await knowledge_search_service.search_knowledge(session, store.id, "helicopter rental")

    assert results == []


@pytest.mark.parametrize("query", ["", "   ", "???"])
async def test_query_without_searchable_words_returns_empty_list(
    session: AsyncSession, store: Store, query: str
) -> None:
    await create_knowledge(session, store, "Is there parking?", "Yes, behind the shop.")

    # 빈 문자열, 공백, 글자가 없는 검색어는 아무것도 찾지 않는다.
    results = await knowledge_search_service.search_knowledge(session, store.id, query)

    assert results == []


async def test_results_are_ordered_by_relevance(session: AsyncSession, store: Store) -> None:
    weak = await create_knowledge(
        session, store, "Do you sell gift cards?", "Yes. Cards can be used to validate parking next door."
    )
    strong = await create_knowledge(session, store, "Is parking available?", "Free parking in the back lot.")

    results = await knowledge_search_service.search_knowledge(session, store.id, "parking")

    # 질문과 답변 모두에 단어가 있는 항목이 답변에만 있는 항목보다 위에 온다.
    assert [r.source_id for r in results] == [strong.id, weak.id]
    assert results[0].relevance_score > results[1].relevance_score > 0


async def test_result_contains_original_question_answer_and_source(session: AsyncSession, store: Store) -> None:
    entry = await create_knowledge(session, store, "Is parking available?", "Free parking in the back lot.")

    results = await knowledge_search_service.search_knowledge(session, store.id, "parking")

    assert len(results) == 1
    result = results[0]
    assert result.source_type == "knowledge"
    assert result.source_id == entry.id
    assert result.question == "Is parking available?"
    assert result.answer == "Free parking in the back lot."
    assert result.relevance_score > 0


async def test_other_store_knowledge_is_not_searched(session: AsyncSession, store: Store) -> None:
    other = await create_store(session, slug="other-store")
    mine = await create_knowledge(session, store, "Is parking available?", "Free lot behind our shop.")
    await create_knowledge(session, other, "Is parking available?", "Use the garage across the street.")

    results = await knowledge_search_service.search_knowledge(session, store.id, "parking")

    assert [r.source_id for r in results] == [mine.id]


async def test_other_store_only_match_returns_empty_list(session: AsyncSession, store: Store) -> None:
    other = await create_store(session, slug="other-store")
    await create_knowledge(session, other, "Is parking available?", "Use the garage across the street.")

    results = await knowledge_search_service.search_knowledge(session, store.id, "parking")

    assert results == []


async def test_limit_caps_number_of_results(session: AsyncSession, store: Store) -> None:
    for number in range(3):
        await create_knowledge(session, store, f"Parking question {number}?", f"Parking answer {number}.")

    results = await knowledge_search_service.search_knowledge(session, store.id, "parking", limit=2)

    assert len(results) == 2

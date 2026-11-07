import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Store
from app.services import knowledge_search_service
from tests.factories import create_knowledge, create_store


async def test_empty_knowledge_returns_empty_list(session: AsyncSession, store: Store) -> None:
    results = await knowledge_search_service.search_knowledge(session, store.id, "parking")

    assert results == []


async def test_exact_question_match(session: AsyncSession, store: Store) -> None:
    entry = await create_knowledge(
        session, store, "What is your refund policy?", "Full refund within 7 days with a receipt."
    )

    results = await knowledge_search_service.search_knowledge(session, store.id, "What is your refund policy?")

    assert [r.source_id for r in results] == [entry.id]


async def test_similar_wording_matches_by_word_form(session: AsyncSession, store: Store) -> None:
    entry = await create_knowledge(
        session, store, "Are dogs allowed on the patio?", "Well-behaved dogs are welcome on the patio."
    )

    # dogs/dog, allowed/allow처럼 어형이 달라도 같은 단어로 찾는다.
    results = await knowledge_search_service.search_knowledge(session, store.id, "dog allow patio")

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


@pytest.mark.parametrize("query", ["", "   ", "what is the"])
async def test_query_without_searchable_words_returns_empty_list(
    session: AsyncSession, store: Store, query: str
) -> None:
    await create_knowledge(session, store, "Is there parking?", "Yes, behind the shop.")

    # 빈 문자열, 공백, 불용어(what is the)만 있는 질문은 아무것도 찾지 않는다.
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

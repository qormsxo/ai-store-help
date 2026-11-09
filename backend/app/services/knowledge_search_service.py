import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import knowledge_repository
from app.schemas import KnowledgeSearchItem

DEFAULT_LIMIT = 5


async def search_knowledge(
    session: AsyncSession, store_id: uuid.UUID, query: str, limit: int = DEFAULT_LIMIT
) -> list[KnowledgeSearchItem]:
    """사장이 쌓아 둔 지식을 검색한다. 결과가 없으면 빈 리스트.

    store_id는 호출하는 백엔드가 현재 매장에서 넘기고, 다른 매장의 지식은 검색되지 않는다.
    결과가 있어도 질문에 답할 수 있다는 뜻은 아니다. 관련 후보일 뿐이므로 답변 가능 여부는
    호출한 쪽이 question과 answer를 읽고 판단한다.
    """
    if not query.strip():
        return []
    rows = await knowledge_repository.search_active_entries(session, store_id, query, limit)
    return [
        KnowledgeSearchItem(
            source_id=entry.id,
            question=entry.question,
            answer=entry.answer,
            # float4 점수의 부동소수점 잡음(0.1 → 0.10000000149)을 없애려고 반올림한다.
            relevance_score=round(score, 4),
        )
        for entry, score in rows
    ]

import uuid

from sqlalchemy import Float, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KnowledgeEntry

# 영어 사전(stemming, 불용어)을 쓴다. dogs와 dog처럼 어형이 다른 표현도 같은 단어로 본다.
_ENGLISH = literal_column("'english'::regconfig")


async def search_active_entries(
    session: AsyncSession, store_id: uuid.UUID, query: str, limit: int
) -> list[tuple[KnowledgeEntry, float]]:
    """해당 매장의 활성 지식을 질문과 답변 전체에서 검색해 관련성 높은 순서로 반환한다.

    질문(A)을 답변(B)보다 높게 가중한다. 사장이 정리한 질문과 맞는 항목이 답변 본문에 단어만
    우연히 들어 있는 항목보다 위로 오게 하기 위해서다.
    """
    # setweight의 가중치는 PostgreSQL "char" 타입이라 바인드 파라미터(VARCHAR) 대신 상수 리터럴로 쓴다.
    document = func.setweight(func.to_tsvector(_ENGLISH, KnowledgeEntry.question), literal_column("'A'")).op(
        "||"
    )(func.setweight(func.to_tsvector(_ENGLISH, KnowledgeEntry.answer), literal_column("'B'")))
    tsquery = func.websearch_to_tsquery(_ENGLISH, query)
    rank = func.ts_rank_cd(document, tsquery, type_=Float)

    statement = (
        select(KnowledgeEntry, rank)
        .where(
            KnowledgeEntry.store_id == store_id,
            KnowledgeEntry.is_active.is_(True),
            document.op("@@")(tsquery),
        )
        # 점수가 같으면 최근에 갱신된 지식을 먼저 둔다. id는 순서를 고정하기 위한 마지막 기준이다.
        .order_by(rank.desc(), KnowledgeEntry.updated_at.desc(), KnowledgeEntry.id)
        .limit(limit)
    )
    result = await session.execute(statement)
    return [(entry, score) for entry, score in result.all()]

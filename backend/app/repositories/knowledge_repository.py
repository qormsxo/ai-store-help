import re
import uuid

from sqlalchemy import Float, SQLColumnExpression, String, func, literal, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KnowledgeEntry

# PostgreSQL 기본 설정에는 한글 형태소 사전이 없다. 영어 사전은 한글을 검색어에서 빼므로 simple을 쓴다.
# simple은 띄어 쓴 단어를 그대로 둔다. 강아지와 강아지는 같은 단어로 보지 않는다.
_TEXT_SEARCH = literal_column("'simple'::regconfig")
_WORD = re.compile(r"[0-9A-Za-z가-힣]+")


def _any_keyword_query(query: str) -> str:
    """모델이 준 검색어를 '하나라도 맞으면 찾는' 웹 검색문으로 바꾼다.

    websearch_to_tsquery는 띄어쓴 단어를 모두 포함한 문서만 찾는다. dog-friendly는 한 덩어리로
    남아 저장된 dog와도 어긋난다. 붙임표와 공백으로 나누고 or로 잇는다.
    """
    words = [word for word in _WORD.findall(query) if word.casefold() != "or"]
    return " or ".join(words)


def _normalized(question: SQLColumnExpression[str]) -> SQLColumnExpression[str]:
    """중복 지식 방지 인덱스(uq_knowledge_entries_store_active_question)와 같은 정규화: 공백과 대소문자 무시."""
    return func.lower(func.regexp_replace(func.btrim(question), r"\s+", " ", "g"))


async def find_active_entry_by_question(
    session: AsyncSession, store_id: uuid.UUID, question: str
) -> KnowledgeEntry | None:
    """같은 매장에서 같은 질문(공백, 대소문자 무시)으로 활성 상태인 지식. 인덱스 때문에 최대 한 건이다."""
    return await session.scalar(
        select(KnowledgeEntry).where(
            KnowledgeEntry.store_id == store_id,
            KnowledgeEntry.is_active.is_(True),
            _normalized(KnowledgeEntry.question) == _normalized(literal(question, String)),
        )
    )


async def search_active_entries(
    session: AsyncSession, store_id: uuid.UUID, query: str, limit: int
) -> list[tuple[KnowledgeEntry, float]]:
    """해당 매장의 활성 지식을 질문과 답변 전체에서 검색해 관련성 높은 순서로 반환한다.

    검색어의 단어가 모두 들어 있을 필요는 없다. 하나라도 맞으면 후보가 되고, 많이 맞을수록 위로 온다.
    질문(A)을 답변(B)보다 높게 가중한다. 사장이 정리한 질문과 맞는 항목이 답변 본문에 단어만
    우연히 들어 있는 항목보다 위로 오게 하기 위해서다.
    """
    keywords = _any_keyword_query(query)
    if not keywords:
        return []
    # setweight의 가중치는 PostgreSQL "char" 타입이라 바인드 파라미터(VARCHAR) 대신 상수 리터럴로 쓴다.
    document = func.setweight(func.to_tsvector(_TEXT_SEARCH, KnowledgeEntry.question), literal_column("'A'")).op(
        "||"
    )(func.setweight(func.to_tsvector(_TEXT_SEARCH, KnowledgeEntry.answer), literal_column("'B'")))
    tsquery = func.websearch_to_tsquery(_TEXT_SEARCH, keywords)
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
    # type_=Float는 점수를 Decimal | float로 추론한다. 이 함수가 돌려주는 점수는 float다.
    return [(entry, float(score)) for entry, score in result.all()]

import re
import uuid

from sqlalchemy import SQLColumnExpression, String, case, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KnowledgeEntry

_WORD = re.compile(r"[0-9A-Za-z가-힣]+")
# 질문에 있으면 답에만 있는 경우보다 위로 올린다.
_QUESTION_WEIGHT = 2
_ANSWER_WEIGHT = 1


def _keywords(query: str) -> list[str]:
    """붙임표와 공백으로 단어를 나눈다. dog-friendly는 dog와 friendly가 된다."""
    return [word for word in _WORD.findall(query) if word.casefold() != "or"]


def _contains_pattern(word: str) -> str:
    """LIKE 와일드카드 문자를 글자 그대로 두고, 앞뒤를 포함 검색으로 만든다."""
    escaped = word.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    return f"%{escaped}%"


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

    검색어의 단어가 모두 들어 있을 필요는 없다. 하나라도 앞뒤로 포함되면 후보가 되고, 많이 맞을수록 위로 온다.
    질문에 들어 있으면 답변에만 들어 있는 경우보다 높게 점수한다. 반려견은 반려견과에도 맞는다.
    """
    words = _keywords(query)
    if not words:
        return []
    # 단어마다 질문 2점, 답변 1점. 둘 다 맞으면 더해진다.
    score: SQLColumnExpression[int] = literal(0)
    hits: list[SQLColumnExpression[bool]] = []
    for word in words:
        pattern = _contains_pattern(word)
        in_question = KnowledgeEntry.question.ilike(pattern, escape="\\")
        in_answer = KnowledgeEntry.answer.ilike(pattern, escape="\\")
        hits.append(in_question | in_answer)
        score = score + case((in_question, _QUESTION_WEIGHT), else_=0) + case((in_answer, _ANSWER_WEIGHT), else_=0)

    statement = (
        select(KnowledgeEntry, score)
        .where(
            KnowledgeEntry.store_id == store_id,
            KnowledgeEntry.is_active.is_(True),
            or_(*hits),
        )
        # 점수가 같으면 최근에 갱신된 지식을 먼저 둔다. id는 순서를 고정하기 위한 마지막 기준이다.
        .order_by(score.desc(), KnowledgeEntry.updated_at.desc(), KnowledgeEntry.id)
        .limit(limit)
    )
    result = await session.execute(statement)
    # 점수는 질문 2점, 답변 1점을 더한 정수다. 호출부가 소수로 비교하므로 float로 맞춘다.
    return [(entry, float(score)) for entry, score in result.all()]

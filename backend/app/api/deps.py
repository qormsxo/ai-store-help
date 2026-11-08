from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.service import AgentService
from app.db import get_session
from app.llm.factory import get_llm_provider
from app.models import Store
from app.services import store_service

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_store(request: Request, session: SessionDep) -> Store:
    # 경로 값은 문자열이다. 키가 없으면 기본 매장이다.
    raw = request.path_params.get("slug")
    slug = None if raw is None else str(raw)
    return await store_service.get_store_for_request(session, slug)


def get_agent_service() -> AgentService:
    """앱 전체가 공유하는 Provider로 AgentService를 만든다. GEMINI 설정이 없으면 LLMNotConfiguredError(503)."""
    return AgentService(get_llm_provider())


AgentServiceDep = Annotated[AgentService, Depends(get_agent_service)]

# 라우터는 이 의존성으로만 매장을 얻는다. 본문이나 쿼리의 store_id는 무시한다.
# 경로가 /stores/{slug}이면 그 매장이고, 아니면 DEFAULT_STORE_SLUG다.
CurrentStoreDep = Annotated[Store, Depends(get_current_store)]

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.service import AgentService
from app.db import get_session
from app.llm.factory import get_llm_provider
from app.models import Store
from app.services import store_service

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_store(session: SessionDep) -> Store:
    return await store_service.get_default_store(session)


def get_agent_service() -> AgentService:
    """앱 전체가 공유하는 Provider로 AgentService를 만든다. GEMINI 설정이 없으면 LLMNotConfiguredError(503)."""
    return AgentService(get_llm_provider())


AgentServiceDep = Annotated[AgentService, Depends(get_agent_service)]

# 라우터는 이 의존성으로만 매장을 얻는다. 요청 본문이나 쿼리로 store_id를 받지 않는다.
CurrentStoreDep = Annotated[Store, Depends(get_current_store)]

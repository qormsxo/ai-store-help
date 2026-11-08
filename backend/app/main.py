import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app.api import conversations, health, owner, store
from app.config import get_settings
from app.db import engine
from app.errors import LLMNotConfiguredError, register_exception_handlers
from app.llm.factory import close_llm_provider, get_llm_provider

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

# 정상적인 시작과 종료를 위해 lifespan 에 선언한다.
@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    try:
        get_llm_provider()
    except LLMNotConfiguredError as exc:
        # 키 없이도 Health Check와 매장 조회는 동작해야 하므로 기동은 계속한다. Agent 호출만 503이 된다.
        logger.warning("LLM provider is not configured: %s", exc.message)
    yield
    await close_llm_provider()
    await engine.dispose()


app = FastAPI(title="AI Store Help", lifespan=lifespan)
register_exception_handlers(app)
# 같은 라우터를 두 접두사에 붙인다. slug가 없는 경로는 기본 매장, /stores/{slug}는 그 매장이다.
scoped = APIRouter(prefix="/stores/{slug}")
scoped.include_router(store.router)
scoped.include_router(conversations.router)
scoped.include_router(owner.router)
app.include_router(health.router, prefix="/api")
app.include_router(store.router, prefix="/api")
app.include_router(conversations.router, prefix="/api")
app.include_router(owner.router, prefix="/api")
app.include_router(scoped, prefix="/api")

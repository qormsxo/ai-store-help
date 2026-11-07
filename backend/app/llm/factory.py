import logging
from functools import lru_cache

from app.config import Settings, get_settings
from app.llm.dev_mock import DevMockProvider
from app.llm.gemini import create_gemini_provider
from app.llm.provider import LLMProvider

logger = logging.getLogger(__name__)


def create_llm_provider(settings: Settings) -> LLMProvider:
    """LLM_PROVIDER=mock이면 개발용 가짜를, gemini면 Gemini를 만든다."""
    if settings.llm_provider == "mock":
        return DevMockProvider()
    return create_gemini_provider(settings)


@lru_cache
def get_llm_provider() -> LLMProvider:
    """앱 전체에서 클라이언트 하나를 공유한다. 설정이 없으면 LLMNotConfiguredError(캐시되지 않음)."""
    return create_llm_provider(get_settings())


async def close_llm_provider() -> None:
    """앱 종료 시 호출한다. 만들어진 클라이언트가 있을 때만 닫는다."""
    if get_llm_provider.cache_info().currsize:
        await get_llm_provider().aclose()
        get_llm_provider.cache_clear()

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import SLUG_PATTERN, get_settings
from app.errors import NotFoundError, StoreNotConfiguredError
from app.models import Store
from app.repositories import store_repository

logger = logging.getLogger(__name__)


async def get_store_by_slug(session: AsyncSession, slug: str) -> Store:
    """slug로 매장과 영업시간을 조회한다. 없으면 StoreNotConfiguredError."""
    store = await store_repository.find_store_by_slug(session, slug)
    if store is None:
        # 클라이언트에는 일반 메시지만 주고, 해결 방법은 서버 로그에 남긴다.
        logger.error("Store %r does not exist. Run `python -m app.seed` to create it.", slug)
        raise StoreNotConfiguredError("매장 정보를 찾을 수 없습니다.")
    return store


async def get_default_store(session: AsyncSession) -> Store:
    """주소에 매장이 없을 때 쓰는 매장(DEFAULT_STORE_SLUG)을 조회한다."""
    return await get_store_by_slug(session, get_settings().default_store_slug)


async def get_store_for_request(session: AsyncSession, slug: str | None) -> Store:
    """경로에 slug가 있으면 그 매장, 없으면 기본 매장. 없는 slug는 404다."""
    if slug is None:
        return await get_default_store(session)
    if SLUG_PATTERN.fullmatch(slug) is None:
        raise NotFoundError("매장을 찾을 수 없습니다.")
    store = await store_repository.find_store_by_slug(session, slug)
    if store is None:
        raise NotFoundError("매장을 찾을 수 없습니다.")
    return store

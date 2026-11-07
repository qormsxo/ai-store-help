from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """요청 단위 DB 세션. 백그라운드 작업은 이 의존성을 쓰지 말고 SessionFactory로 직접 세션을 연다."""
    async with SessionFactory() as session:
        yield session

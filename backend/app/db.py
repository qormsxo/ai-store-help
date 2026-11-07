from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

# 세션 타임존을 UTC로 고정해서 now()와 시간 비교가 서버 설정에 흔들리지 않게 한다.
engine = create_async_engine(
    get_settings().database_url,
    pool_pre_ping=True,
    connect_args={"server_settings": {"timezone": "UTC"}},
)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """요청 단위 DB 세션. 커밋은 서비스 함수가 명시적으로 한다.

    백그라운드 작업은 이 의존성을 쓰지 말고 SessionFactory로 직접 세션을 연다.
    """
    async with SessionFactory() as session:
        yield session

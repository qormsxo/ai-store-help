import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from alembic import command
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.main import app
from app.models import Conversation, Store
from tests.db_utils import (
    UTC_SESSION,
    alembic_config,
    create_database_if_missing,
    drop_database_if_exists,
    recreate_database,
    scratch_database_url,
    database_url_for_tests,
)
from tests.factories import create_conversation, create_store


@pytest.fixture(scope="session")
def migrated_database_url() -> str:
    """테스트 DB를 만들고 Alembic으로 스키마를 구성한다.

    downgrade → upgrade를 매번 실행하므로 마이그레이션의 양방향 동작도 함께 검증된다.
    """
    url = database_url_for_tests()
    asyncio.run(create_database_if_missing(url))

    config = alembic_config(url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    return url.render_as_string(hide_password=False)


@pytest.fixture
async def session(migrated_database_url: str) -> AsyncIterator[AsyncSession]:
    """테스트마다 바깥 트랜잭션을 열고 끝에서 롤백한다. 테스트 안의 commit()은 savepoint로 처리된다."""
    engine = create_async_engine(migrated_database_url, poolclass=NullPool, connect_args=UTC_SESSION)
    try:
        async with engine.connect() as connection:
            outer = await connection.begin()
            async with AsyncSession(
                bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
            ) as db_session:
                yield db_session
            await outer.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
async def store(session: AsyncSession) -> Store:
    """테스트 트랜잭션 안의 기본 매장. 테스트가 끝나면 롤백된다."""
    return await create_store(session)


@pytest.fixture
async def conversation(session: AsyncSession, store: Store) -> Conversation:
    return await create_conversation(session, store)


@pytest.fixture
async def scratch_database() -> AsyncIterator[str]:
    """스키마가 전혀 없는 빈 DB. 마이그레이션을 단계별로 올리고 내리는 테스트가 쓴다."""
    url = scratch_database_url()
    await recreate_database(url)
    try:
        yield url.render_as_string(hide_password=False)
    finally:
        await drop_database_if_exists(url)


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()

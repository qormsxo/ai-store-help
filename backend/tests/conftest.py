import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.main import app

BACKEND_DIR = Path(__file__).resolve().parents[1]
UTC_SESSION = {"server_settings": {"timezone": "UTC"}}


def _test_database_url() -> URL:
    """개발 DB와 같은 서버의 `<DB이름>_test` 데이터베이스. 개발 데이터를 건드리지 않는다."""
    url = make_url(get_settings().database_url)
    return url.set(database=f"{url.database}_test")


async def _create_database_if_missing(url: URL) -> None:
    admin = create_async_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        async with admin.connect() as connection:
            exists = await connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
            )
            if not exists:
                await connection.execute(text(f'CREATE DATABASE "{url.database}"'))
    finally:
        await admin.dispose()


@pytest.fixture(scope="session")
def migrated_database_url() -> str:
    """테스트 DB를 만들고 Alembic으로 스키마를 구성한다.

    downgrade → upgrade를 매번 실행하므로 마이그레이션의 양방향 동작도 함께 검증된다.
    """
    url = _test_database_url()
    asyncio.run(_create_database_if_missing(url))

    rendered = url.render_as_string(hide_password=False)
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", rendered.replace("%", "%%"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    return rendered


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
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()

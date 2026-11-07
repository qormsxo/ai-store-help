from pathlib import Path

from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings

BACKEND_DIR = Path(__file__).resolve().parents[1]
UTC_SESSION = {"server_settings": {"timezone": "UTC"}}


def database_url_for_tests() -> URL:
    """개발 DB와 같은 서버의 `<DB이름>_test` 데이터베이스. 개발 데이터를 건드리지 않는다."""
    url = make_url(get_settings().database_url)
    return url.set(database=f"{url.database}_test")


def scratch_database_url() -> URL:
    """마이그레이션 데이터 보존 테스트처럼 스키마를 직접 올리고 내리는 테스트 전용 DB."""
    url = database_url_for_tests()
    return url.set(database=f"{url.database}_scratch")


def alembic_config(url: URL) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    rendered = url.render_as_string(hide_password=False)
    # configparser 보간 때문에 %는 %%로 이스케이프해야 한다.
    config.set_main_option("sqlalchemy.url", rendered.replace("%", "%%"))
    return config


async def create_database_if_missing(url: URL) -> None:
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


async def recreate_database(url: URL) -> None:
    """데이터베이스를 지우고 빈 상태로 다시 만든다."""
    await drop_database_if_exists(url)
    await create_database_if_missing(url)


async def drop_database_if_exists(url: URL) -> None:
    admin = create_async_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
    finally:
        await admin.dispose()

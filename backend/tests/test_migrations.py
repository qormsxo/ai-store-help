import asyncio
import uuid

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.models import Base
from tests.db_utils import alembic_config


def _schema_differences(connection: Connection) -> list[str]:
    context = MigrationContext.configure(connection, opts={"compare_type": True})
    return [str(difference) for difference in compare_metadata(context, Base.metadata)]


async def test_migrated_schema_matches_models(session: AsyncSession) -> None:
    """모델을 고치고 마이그레이션을 빠뜨리면 이 테스트가 실패한다."""
    connection = await session.connection()

    differences = await connection.run_sync(_schema_differences)

    assert differences == []


async def _run_alembic(action: str, url: str, revision: str) -> None:
    config = alembic_config(make_url(url))
    runner = command.upgrade if action == "upgrade" else command.downgrade
    # env.py가 asyncio.run을 쓰므로 이미 이벤트 루프가 도는 테스트에서는 별도 스레드에서 실행한다.
    await asyncio.to_thread(runner, config, revision)


async def _scalar(url: str, sql: str, params: dict[str, uuid.UUID] | None = None) -> str | int | uuid.UUID | None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            return await connection.scalar(text(sql), params or {})
    finally:
        await engine.dispose()


async def _insert_legacy_rows(url: str) -> None:
    """0001 스키마(store_id 없음)에 대화, 티켓, 지식을 넣는다."""
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            conversation_id = await connection.scalar(
                text("INSERT INTO conversations (visitor_id) VALUES ('legacy-visitor') RETURNING id")
            )
            ticket_id = await connection.scalar(
                text(
                    "INSERT INTO support_tickets (conversation_id, question, escalation_reason)"
                    " VALUES (:cid, 'legacy question', 'no_knowledge') RETURNING id"
                ),
                {"cid": conversation_id},
            )
            await connection.execute(
                text("INSERT INTO knowledge_entries (question, answer, source_ticket_id) VALUES ('q1', 'a1', :tid)"),
                {"tid": ticket_id},
            )
            await connection.execute(text("INSERT INTO knowledge_entries (question, answer) VALUES ('q2', 'a2')"))
    finally:
        await engine.dispose()


async def test_0002_assigns_existing_rows_to_a_placeholder_store(scratch_database: str) -> None:
    await _run_alembic("upgrade", scratch_database, "0001")
    await _insert_legacy_rows(scratch_database)

    await _run_alembic("upgrade", scratch_database, "0002")

    slug = get_settings().default_store_slug
    assert await _scalar(scratch_database, "SELECT count(*) FROM stores") == 1
    assert await _scalar(scratch_database, "SELECT slug FROM stores") == slug
    for table in ("conversations", "support_tickets", "knowledge_entries"):
        orphans = await _scalar(scratch_database, f"SELECT count(*) FROM {table} WHERE store_id IS NULL")
        assert orphans == 0, f"{table}에 store_id가 비어 있는 행이 있다"
        total = await _scalar(scratch_database, f"SELECT count(*) FROM {table}")
        assert total is not None and total > 0


async def test_0002_enforces_not_null_after_backfill(scratch_database: str) -> None:
    await _run_alembic("upgrade", scratch_database, "0002")

    nullable_columns = await _scalar(
        scratch_database,
        "SELECT count(*) FROM information_schema.columns"
        " WHERE column_name = 'store_id' AND is_nullable = 'YES'"
        " AND table_name IN ('conversations', 'support_tickets', 'knowledge_entries')",
    )

    assert nullable_columns == 0


async def test_0002_creates_no_store_when_database_is_empty(scratch_database: str) -> None:
    await _run_alembic("upgrade", scratch_database, "0001")

    await _run_alembic("upgrade", scratch_database, "0002")

    assert await _scalar(scratch_database, "SELECT count(*) FROM stores") == 0


async def test_0002_downgrade_keeps_existing_rows(scratch_database: str) -> None:
    await _run_alembic("upgrade", scratch_database, "0001")
    await _insert_legacy_rows(scratch_database)
    await _run_alembic("upgrade", scratch_database, "0002")

    await _run_alembic("downgrade", scratch_database, "0001")

    assert await _scalar(scratch_database, "SELECT count(*) FROM conversations") == 1
    assert await _scalar(scratch_database, "SELECT count(*) FROM knowledge_entries") == 2
    has_store_id = await _scalar(
        scratch_database,
        "SELECT count(*) FROM information_schema.columns WHERE table_name = 'conversations' AND column_name = 'store_id'",
    )
    assert has_store_id == 0

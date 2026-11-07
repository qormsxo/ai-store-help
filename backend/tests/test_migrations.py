from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Base


def _schema_differences(connection: Connection) -> list[str]:
    context = MigrationContext.configure(connection, opts={"compare_type": True})
    return [str(difference) for difference in compare_metadata(context, Base.metadata)]


async def test_migrated_schema_matches_models(session: AsyncSession) -> None:
    """모델을 고치고 마이그레이션을 빠뜨리면 이 테스트가 실패한다."""
    connection = await session.connection()

    differences = await connection.run_sync(_schema_differences)

    assert differences == []

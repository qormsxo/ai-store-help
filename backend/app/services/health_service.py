import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ServiceUnavailableError

logger = logging.getLogger(__name__)


async def check_database(session: AsyncSession) -> None:
    """DB에 접속해 SELECT 1이 성공하는지 확인한다. 실패하면 ServiceUnavailableError."""
    try:
        await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError) as exc:
        logger.warning("Database health check failed: %s", exc)
        raise ServiceUnavailableError("데이터베이스에 연결할 수 없습니다.") from exc

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Store


async def find_store_by_slug(session: AsyncSession, slug: str) -> Store | None:
    """영업시간(요일 순)까지 함께 로드해서 반환한다."""
    return await session.scalar(
        select(Store).where(Store.slug == slug).options(selectinload(Store.business_hours))
    )


async def find_store_by_id(session: AsyncSession, store_id: uuid.UUID) -> Store | None:
    return await session.scalar(
        select(Store).where(Store.id == store_id).options(selectinload(Store.business_hours))
    )

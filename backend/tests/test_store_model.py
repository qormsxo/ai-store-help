import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Store
from tests.factories import create_store


async def test_duplicate_slug_is_rejected(session: AsyncSession, store: Store) -> None:
    with pytest.raises(IntegrityError, match="uq_stores_slug"):
        await create_store(session, slug=store.slug)

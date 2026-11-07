import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SupportTicket


async def find_ticket_by_source_message(session: AsyncSession, source_message_id: uuid.UUID) -> SupportTicket | None:
    return await session.scalar(select(SupportTicket).where(SupportTicket.source_message_id == source_message_id))

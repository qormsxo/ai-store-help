import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SupportTicket, TicketStatus


async def find_ticket_by_source_message(session: AsyncSession, source_message_id: uuid.UUID) -> SupportTicket | None:
    return await session.scalar(select(SupportTicket).where(SupportTicket.source_message_id == source_message_id))


async def find_ticket_in_store(
    session: AsyncSession, store_id: uuid.UUID, ticket_id: uuid.UUID, *, for_update: bool = False
) -> SupportTicket | None:
    """다른 매장의 티켓은 id가 맞아도 찾지 않는다.

    for_update이면 행을 잠그고 DB의 최신 값으로 다시 읽는다. 같은 티켓에 답변이 동시에 들어와도
    하나씩 처리되고, 나중 요청은 먼저 처리된 결과(resolved)를 보게 된다.
    """
    statement = select(SupportTicket).where(SupportTicket.id == ticket_id, SupportTicket.store_id == store_id)
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return await session.scalar(statement)


async def list_tickets(
    session: AsyncSession, store_id: uuid.UUID, status: TicketStatus | None
) -> list[SupportTicket]:
    """오래된 문의부터 반환한다(인덱스 ix_support_tickets_store_id_status_created_at 순서)."""
    statement = select(SupportTicket).where(SupportTicket.store_id == store_id)
    if status is not None:
        statement = statement.where(SupportTicket.status == status)
    result = await session.scalars(statement.order_by(SupportTicket.created_at, SupportTicket.id))
    return list(result.all())

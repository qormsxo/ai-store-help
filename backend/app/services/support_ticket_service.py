import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, EscalationReason, SupportTicket
from app.repositories import support_ticket_repository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TicketCreation:
    ticket: SupportTicket
    # False면 같은 고객 메시지로 이미 만들어진 티켓을 돌려준 것이다.
    created: bool


async def create_ticket(
    session: AsyncSession,
    conversation: Conversation,
    source_message_id: uuid.UUID,
    question: str,
    escalation_reason: EscalationReason,
) -> TicketCreation:
    """고객 메시지 하나에 대해 사장에게 넘길 티켓을 만든다. 같은 메시지로 두 번 부르면 기존 티켓을 돌려준다.

    store_id는 대화에서 복사한다. 호출하는 쪽(LLM 포함)이 매장을 고를 수 없다.
    """
    existing = await support_ticket_repository.find_ticket_by_source_message(session, source_message_id)
    if existing is not None:
        return TicketCreation(ticket=existing, created=False)

    ticket = SupportTicket(
        store_id=conversation.store_id,
        conversation_id=conversation.id,
        source_message_id=source_message_id,
        question=question,
        escalation_reason=escalation_reason,
    )
    session.add(ticket)
    await session.flush()
    logger.info("Support ticket %s created for conversation %s", ticket.id, conversation.id)
    return TicketCreation(ticket=ticket, created=True)

import uuid

from fastapi import APIRouter

from app.api.deps import CurrentStoreDep, SessionDep
from app.models import TicketStatus
from app.schemas import (
    ConversationResponse,
    MessageResponse,
    OwnerReplyCreate,
    OwnerReplyResponse,
    TicketConversationResponse,
    TicketDetailResponse,
    TicketResponse,
)
from app.services import owner_ticket_service

router = APIRouter(prefix="/owner", tags=["owner"])


@router.get("/tickets", response_model=list[TicketResponse])
async def list_tickets(
    store: CurrentStoreDep, session: SessionDep, status: TicketStatus | None = None
) -> list[TicketResponse]:
    tickets = await owner_ticket_service.list_tickets(session, store.id, status)
    return [TicketResponse.model_validate(ticket) for ticket in tickets]


@router.get("/tickets/{ticket_id}", response_model=TicketDetailResponse)
async def get_ticket(ticket_id: uuid.UUID, store: CurrentStoreDep, session: SessionDep) -> TicketDetailResponse:
    detail = await owner_ticket_service.get_ticket_detail(session, store.id, ticket_id)
    conversation = TicketConversationResponse(
        **ConversationResponse.model_validate(detail.conversation).model_dump(),
        messages=[MessageResponse.model_validate(message) for message in detail.messages],
    )
    return TicketDetailResponse(
        **TicketResponse.model_validate(detail.ticket).model_dump(),
        source_message_id=detail.ticket.source_message_id,
        conversation=conversation,
    )


@router.post("/tickets/{ticket_id}/reply", response_model=OwnerReplyResponse)
async def reply_to_ticket(
    ticket_id: uuid.UUID, body: OwnerReplyCreate, store: CurrentStoreDep, session: SessionDep
) -> OwnerReplyResponse:
    result = await owner_ticket_service.reply_to_ticket(session, store.id, ticket_id, body.answer)
    return OwnerReplyResponse(
        ticket=TicketResponse.model_validate(result.ticket),
        knowledge_entry_id=result.knowledge_entry.id,
        superseded_knowledge_id=result.superseded_knowledge_id,
        conversation_message=MessageResponse.model_validate(result.conversation_message),
    )

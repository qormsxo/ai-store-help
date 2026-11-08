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
    """GET /api/owner/tickets. 기본 매장의 문의를 오래된 순으로 돌려준다.

    status=open 또는 status=resolved가 있으면 그 상태만 돌려준다. 없으면 전체다.
    """
    tickets = await owner_ticket_service.list_tickets(session, store.id, status)
    return [TicketResponse.model_validate(ticket) for ticket in tickets]


@router.get("/tickets/{ticket_id}", response_model=TicketDetailResponse)
async def get_ticket(ticket_id: uuid.UUID, store: CurrentStoreDep, session: SessionDep) -> TicketDetailResponse:
    """GET /api/owner/tickets/{ticket_id}. 문의 하나와 그 고객 대화의 메시지를 돌려준다.

    이 매장 티켓이 아니면 404다.
    """
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
    """POST /api/owner/tickets/{ticket_id}/reply. 사장 답변을 저장하고 그 결과를 돌려준다.

    지식으로 저장하고, 티켓을 resolved로 바꾸고, 고객 대화에 assistant 메시지를 추가한다. 이 세 가지는 한 트랜잭션이다.
    """
    result = await owner_ticket_service.reply_to_ticket(session, store.id, ticket_id, body.answer)
    return OwnerReplyResponse(
        ticket=TicketResponse.model_validate(result.ticket),
        knowledge_entry_id=result.knowledge_entry.id,
        superseded_knowledge_id=result.superseded_knowledge_id,
        conversation_message=MessageResponse.model_validate(result.conversation_message),
    )

"""사장 화면의 문의 처리. 함수는 목록, 상세, 답변 세 개다. LLM은 호출하지 않는다."""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ServiceUnavailableError, TicketAlreadyResolvedError, TicketNotFoundError
from app.models import Conversation, KnowledgeEntry, Message, MessageRole, SupportTicket, TicketStatus, utcnow
from app.repositories import support_ticket_repository
from app.services import conversation_service, knowledge_service

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TicketDetail:
    """문의 하나와 그 고객 대화, 메시지. API 응답이 아니라 라우터에 넘기는 값이다."""

    ticket: SupportTicket
    conversation: Conversation
    messages: list[Message]


@dataclass(frozen=True)
class ReplyResult:
    """답변이 커밋된 뒤의 결과. 라우터가 OwnerReplyResponse로 바꾼다."""

    ticket: SupportTicket
    knowledge_entry: KnowledgeEntry
    superseded_knowledge_id: uuid.UUID | None
    conversation_message: Message


async def list_tickets(session: AsyncSession, store_id: uuid.UUID, status: TicketStatus | None) -> list[SupportTicket]:
    """이 매장의 문의를 오래된 순으로 돌려준다. status가 있으면 그 상태만 고른다.

    조회만 한다. 커밋하지 않고, repository 결과를 그대로 돌려준다.
    """
    return await support_ticket_repository.list_tickets(session, store_id, status)


async def get_ticket_detail(session: AsyncSession, store_id: uuid.UUID, ticket_id: uuid.UUID) -> TicketDetail:
    """문의 하나와 그 대화의 메시지를 모아 TicketDetail로 돌려준다.

    이 매장 티켓이 아니면 TicketNotFoundError다. 조회만 하고 커밋하지 않는다.
    """
    ticket = await support_ticket_repository.find_ticket_in_store(session, store_id, ticket_id)
    if ticket is None:
        raise TicketNotFoundError("문의를 찾을 수 없습니다.")
    conversation = await conversation_service.get_conversation(session, store_id, ticket.conversation_id)
    messages = await conversation_service.list_messages(session, conversation)
    return TicketDetail(ticket=ticket, conversation=conversation, messages=messages)


async def reply_to_ticket(
    session: AsyncSession, store_id: uuid.UUID, ticket_id: uuid.UUID, answer: str
) -> ReplyResult:
    """사장 답변을 처리한다. 이 함수가 커밋한다.

    아래 세 가지는 하나의 트랜잭션이라서 전부 반영되거나 전부 반영되지 않는다.
    1. 질문과 답변을 지식으로 저장
    2. 티켓을 resolved로 변경 (답변과 해결 시각 기록)
    3. 원래 고객 대화에 답변을 AI Assistant 메시지로 추가
    티켓 행을 먼저 잠그므로 같은 티켓에 답변이 동시에 들어와도 하나만 처리되고 나머지는 409다.
    사장과 고객이 직접 대화하는 기능은 없다. 사장의 답변은 AI 메시지 형태로만 고객에게 전달된다.
    """
    try:
        # 티켓 조회
        ticket = await support_ticket_repository.find_ticket_in_store(session, store_id, ticket_id, for_update=True)
        if ticket is None:
            raise TicketNotFoundError("문의를 찾을 수 없습니다.")
        if ticket.status is TicketStatus.RESOLVED:
            raise TicketAlreadyResolvedError("이미 답변이 등록된 문의입니다.")

        # 지식 저장
        saved = await knowledge_service.save_owner_answer(session, ticket, answer)
        # 티켓 업데이트
        ticket.owner_answer = answer
        ticket.status = TicketStatus.RESOLVED
        ticket.resolved_at = utcnow()
        # 고객 대화 조회
        conversation = await conversation_service.get_conversation(session, store_id, ticket.conversation_id)
        # 고객 대화에 assistant 메시지 추가
        message = await conversation_service.add_message(session, conversation, MessageRole.ASSISTANT, answer)
        await session.commit()
    except SQLAlchemyError as exc:
        await session.rollback()
        # SQL 파라미터에 답변 내용이 들어 있을 수 있어 예외 메시지 대신 종류만 남긴다.
        logger.error("Failed to save owner reply: ticket=%s error=%s", ticket_id, type(exc).__name__)
        raise ServiceUnavailableError("답변을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요.") from exc
    except Exception:
        # 404, 409도 여기서 롤백한다. 잠가 둔 티켓 행을 놓아 주기 위해서다.
        await session.rollback()
        raise

    logger.info("Ticket %s resolved: knowledge=%s", ticket.id, saved.entry.id)
    return ReplyResult(
        ticket=ticket,
        knowledge_entry=saved.entry,
        superseded_knowledge_id=saved.superseded_entry_id,
        conversation_message=message,
    )

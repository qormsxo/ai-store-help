import uuid

from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from app.api import sse
from app.api.deps import AgentServiceDep, CurrentStoreDep, SessionDep
from app.schemas import (
    ConversationCreate,
    ConversationResponse,
    ConversationSummaryResponse,
    MessageCreate,
    MessageResponse,
)
from app.services import conversation_service, reply_service

router = APIRouter(tags=["conversations"])


@router.get("/conversations", response_model=list[ConversationSummaryResponse])
async def list_conversations(
    store: CurrentStoreDep,
    session: SessionDep,
    visitor_id: Annotated[str, Query(min_length=1, max_length=64)],
) -> list[ConversationSummaryResponse]:
    """이 매장에서 그 손님의 대화를 최근 활동순으로 돌려준다. 다른 손님, 다른 매장은 빠진다."""
    rows = await conversation_service.list_conversations(session, store.id, visitor_id.strip())
    return [
        ConversationSummaryResponse(
            id=conversation.id,
            visitor_id=conversation.visitor_id,
            created_at=conversation.created_at,
            last_message=last_message,
        )
        for conversation, last_message in rows
    ]


@router.post("/conversations", response_model=ConversationResponse, status_code=201)
async def create_conversation(
    body: ConversationCreate, store: CurrentStoreDep, session: SessionDep
) -> ConversationResponse:
    """이 손님의 새 대화를 만들고 커밋한 뒤 201로 돌려준다."""
    conversation = await conversation_service.create_conversation(session, store, body.visitor_id)
    await session.commit()
    return ConversationResponse.model_validate(conversation)


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: uuid.UUID, store: CurrentStoreDep, session: SessionDep
) -> ConversationResponse:
    """대화 하나를 조회한다. 이 매장 것이 아니면 없는 대화와 같이 404다."""
    conversation = await conversation_service.get_conversation(session, store.id, conversation_id)
    return ConversationResponse.model_validate(conversation)


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageResponse])
async def list_messages(
    conversation_id: uuid.UUID, store: CurrentStoreDep, session: SessionDep
) -> list[MessageResponse]:
    """그 대화의 고객·AI 메시지를 시간순으로 돌려준다. 화면이 이전 기록을 불러올 때 쓴다."""
    conversation = await conversation_service.get_conversation(session, store.id, conversation_id)
    messages = await conversation_service.list_messages(session, conversation)
    return [MessageResponse.model_validate(message) for message in messages]


@router.post("/conversations/{conversation_id}/messages/stream", response_class=StreamingResponse)
async def stream_message(
    conversation_id: uuid.UUID,
    body: MessageCreate,
    request: Request,
    store: CurrentStoreDep,
    session: SessionDep,
    agent: AgentServiceDep,
) -> StreamingResponse:
    """같은 질문에 답을 SSE로 스트리밍한다. 이벤트: message_delta(텍스트 조각), completed(저장된 message_id), error.

    대화가 없거나 요청이 잘못된 경우는 스트림을 시작하기 전에 일반 오류 응답(404, 422)으로 끝난다.
    고객 메시지는 스트림을 시작하기 전에 저장하고 커밋한다.
    """
    stored = await reply_service.save_customer_message(session, store.id, conversation_id, body.content)
    events = reply_service.stream_reply(session, agent, stored)
    return StreamingResponse(
        sse.stream_chat_events(request, events),
        media_type="text/event-stream",
        # 프록시가 응답을 모아서 보내면 스트리밍이 아니게 된다.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

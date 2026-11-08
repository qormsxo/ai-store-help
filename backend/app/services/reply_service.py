"""질문 한 번에 대한 답변 흐름. 대화와 메시지 기록은 conversation_service가 맡는다."""

import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass

import anyio
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.service import AgentReply, AgentService, ToolExecution
from app.errors import AgentEmptyResponseError, MessageStorageError
from app.llm.types import TextDelta
from app.models import Conversation, Message, MessageRole
from app.repositories import knowledge_repository
from app.services import conversation_service

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoredCustomerMessage:
    """저장(커밋)이 끝난 고객 메시지와 그 대화. 이 뒤의 어떤 실패도 이 메시지를 지우지 않는다."""

    conversation: Conversation
    message: Message


@dataclass(frozen=True)
class ReplyCompleted:
    """스트리밍 답변이 Assistant 메시지 하나로 저장(커밋)되었다."""

    assistant_message: Message
    tool_executions: list[ToolExecution]


type ReplyStreamEvent = TextDelta | ReplyCompleted


async def save_customer_message(
    session: AsyncSession, store_id: uuid.UUID, conversation_id: uuid.UUID, content: str
) -> StoredCustomerMessage:
    """고객 메시지를 저장하고 바로 커밋한다. 이후 무엇이 실패해도 고객이 보낸 원문은 남는다.

    답변 생성에 실패한 뒤 고객이 같은 메시지를 다시 보내면 메시지를 새로 저장하지 않고 남아 있는 메시지로 재시도한다.
    """
    conversation = await conversation_service.get_conversation(session, store_id, conversation_id)
    try:
        user_message = await conversation_service.get_or_add_user_message(session, conversation, content)
        await session.commit()
    except SQLAlchemyError as exc:
        await session.rollback()
        raise _storage_error("customer message", conversation_id, exc) from exc
    return StoredCustomerMessage(conversation, user_message)


async def stream_reply(
    session: AsyncSession,
    agent: AgentService,
    stored: StoredCustomerMessage,
    history_limit: int = conversation_service.HISTORY_LIMIT,
) -> AsyncGenerator[ReplyStreamEvent]:
    """저장된 고객 메시지에 스트리밍으로 답한다. 답변 텍스트를 TextDelta로 내보내고, 저장하면 ReplyCompleted를 낸다.

    답변은 끝까지 생성된 뒤에 Assistant 메시지 하나로 저장한다. 조각마다 저장하지 않는다.
    생성이 실패하거나 호출하는 쪽이 중간에 멈추면(연결 종료) 저장하지 않고 롤백한다. 불완전한 답변이 완료된
    메시지로 남지 않고, Tool이 만든 티켓도 함께 사라진다. 고객 메시지는 1단계에서 이미 커밋되어 남는다.
    """
    conversation_id = stored.conversation.id
    committed = False
    completed: ReplyCompleted | None = None
    try:
        reply: AgentReply | None = None
        # 검색은 모델이 고른 단어로 한다. 단어가 저장된 문장과 어긋나면 결과가 비고, 모델은 지식이 없다고 보고 티켓을 만든다.
        # 고객 문장이 활성 지식의 질문과 같으면(대소문자, 연속 공백 무시) 그 답을 그대로 쓰고 모델은 부르지 않는다.
        known = await knowledge_repository.find_active_entry_by_question(
            session, stored.conversation.store_id, stored.message.content
        )
        if known is not None:
            yield TextDelta(text=known.answer)
            reply = AgentReply(content=known.answer, tool_executions=[])
        else:
            # 방금 저장한 고객 메세지는 제외하고 최근 대화 내역을 가져온다.
            history = await conversation_service.get_recent_dialogue(
                session, stored.conversation, stored.message, history_limit
            )
            async with aclosing(agent.reply_stream(session, stored.conversation, stored.message, history)) as events:
                async for event in events:
                    match event:
                        case TextDelta():
                            yield event
                        case AgentReply():
                            reply = event
        if reply is None:
            raise AgentEmptyResponseError("AI가 답변을 만들지 못했습니다. 다시 시도해 주세요.")
        # 답변을 다 받은 뒤의 저장은 취소로 끊기지 않게 한다. 커밋 도중에 끊기면 연결 상태가 어긋날 수 있다.
        with anyio.CancelScope(shield=True):
            assistant_message = await conversation_service.add_message(
                session, stored.conversation, MessageRole.ASSISTANT, reply.content
            )
            await session.commit()
            committed = True
            completed = ReplyCompleted(assistant_message, reply.tool_executions)
    except SQLAlchemyError as exc:
        raise _storage_error("assistant reply", conversation_id, exc) from exc
    finally:
        if not committed:
            # 오류와 취소 모두 여기로 온다. 취소 중에도 롤백은 끝까지 해서 연결을 깨끗하게 돌려준다.
            with anyio.CancelScope(shield=True):
                await session.rollback()
    if completed is not None:
        yield completed


def _storage_error(what: str, conversation_id: uuid.UUID, exc: SQLAlchemyError) -> MessageStorageError:
    # SQL 파라미터에 고객 문장이 들어 있을 수 있어 예외 메시지 대신 종류만 남긴다.
    logger.error("Failed to save %s: conversation=%s error=%s", what, conversation_id, type(exc).__name__)
    return MessageStorageError("메시지를 저장하지 못했습니다. 같은 메시지를 다시 보내 주세요.")

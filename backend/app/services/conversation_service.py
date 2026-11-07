import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ConversationClosedError, ConversationNotFoundError
from app.models import Conversation, ConversationStatus, Message, MessageRole, Store
from app.repositories import conversation_repository

logger = logging.getLogger(__name__)

# 고객이 보는 대화이자 Agent에 넘기는 대화. system 메시지는 고객과 주고받은 내용이 아니므로 제외한다.
_DIALOGUE_ROLES = (MessageRole.USER, MessageRole.ASSISTANT)

# Agent에 넘기는 이전 대화 메시지 수. 대화가 길어져도 최근 메시지 중심으로 제한한다.
HISTORY_LIMIT = 20


async def create_conversation(session: AsyncSession, store: Store, visitor_id: str) -> Conversation:
    """새 대화를 만든다. visitor_id는 익명 방문자 구분용이고 인증 수단이 아니다. 커밋은 호출하는 쪽이 한다."""
    conversation = Conversation(store_id=store.id, visitor_id=visitor_id)
    session.add(conversation)
    await session.flush()
    return conversation


_PREVIEW_LENGTH = 80


async def list_conversations(
    session: AsyncSession, store_id: uuid.UUID, visitor_id: str
) -> list[tuple[Conversation, str | None]]:
    """이 매장에서 그 손님의 대화 목록. 미리보기는 마지막 고객/AI 메시지의 앞부분이다."""
    rows = await conversation_repository.list_conversations_for_visitor(
        session, store_id, visitor_id, _DIALOGUE_ROLES
    )
    return [(conversation, _preview(last_message)) for conversation, last_message in rows]


def _preview(content: str | None) -> str | None:
    if content is None or len(content) <= _PREVIEW_LENGTH:
        return content
    return content[: _PREVIEW_LENGTH - 1] + "…"


async def get_conversation(session: AsyncSession, store_id: uuid.UUID, conversation_id: uuid.UUID) -> Conversation:
    """현재 매장의 대화만 조회한다. 없거나 다른 매장의 대화면 같은 404다(존재 여부를 알려주지 않는다)."""
    conversation = await conversation_repository.find_conversation_in_store(session, store_id, conversation_id)
    if conversation is None:
        raise ConversationNotFoundError("대화를 찾을 수 없습니다.")
    return conversation


async def get_open_conversation(session: AsyncSession, store_id: uuid.UUID, conversation_id: uuid.UUID) -> Conversation:
    conversation = await get_conversation(session, store_id, conversation_id)
    if conversation.status is ConversationStatus.CLOSED:
        raise ConversationClosedError("종료된 대화입니다.")
    return conversation


async def list_messages(session: AsyncSession, conversation: Conversation) -> list[Message]:
    return await conversation_repository.list_messages(session, conversation.id, _DIALOGUE_ROLES)


async def get_recent_dialogue(
    session: AsyncSession, conversation: Conversation, current_message: Message, limit: int = HISTORY_LIMIT
) -> list[Message]:
    """현재 대화의 최근 메시지(오래된 순). 지금 처리 중인 고객 메시지는 이미 저장되어 있으므로 뺀다."""
    return await conversation_repository.list_recent_messages(
        session, conversation.id, _DIALOGUE_ROLES, limit, exclude_message_id=current_message.id
    )


async def add_message(session: AsyncSession, conversation: Conversation, role: MessageRole, content: str) -> Message:
    message = Message(conversation_id=conversation.id, role=role, content=content)
    session.add(message)
    await session.flush()
    return message


async def get_or_add_user_message(session: AsyncSession, conversation: Conversation, content: str) -> Message:
    """고객 메시지를 저장한다. 같은 내용의 답변 없는 메시지가 이미 있으면 새로 저장하지 않고 그것을 돌려준다.

    답변 생성에 실패하면 고객 메시지만 남는다. 고객이 같은 메시지를 다시 보내는 것은 재시도이므로
    메시지를 한 번 더 저장하면 대화에 같은 질문이 두 번 쌓인다. 이미 답을 받은 질문을 다시 보내는 것은
    새 메시지다.
    """
    latest = await conversation_repository.list_recent_messages(session, conversation.id, _DIALOGUE_ROLES, 1)
    if latest and latest[0].role is MessageRole.USER and latest[0].content == content:
        logger.info("Reusing unanswered message %s in conversation %s", latest[0].id, conversation.id)
        return latest[0]
    return await add_message(session, conversation, MessageRole.USER, content)

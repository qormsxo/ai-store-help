from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Conversation,
    EscalationReason,
    KnowledgeEntry,
    Message,
    MessageRole,
    SupportTicket,
)


async def create_conversation(session: AsyncSession, visitor_id: str = "visitor-1") -> Conversation:
    conversation = Conversation(visitor_id=visitor_id)
    session.add(conversation)
    await session.flush()
    return conversation


async def create_message(
    session: AsyncSession,
    conversation: Conversation,
    content: str = "영업시간이 어떻게 되나요?",
    role: MessageRole = MessageRole.USER,
) -> Message:
    message = Message(conversation_id=conversation.id, role=role, content=content)
    session.add(message)
    await session.flush()
    return message


async def create_ticket(
    session: AsyncSession,
    conversation: Conversation,
    source_message: Message | None = None,
    question: str = "영업시간 문의",
) -> SupportTicket:
    ticket = SupportTicket(
        conversation_id=conversation.id,
        source_message_id=source_message.id if source_message else None,
        question=question,
        escalation_reason=EscalationReason.NO_KNOWLEDGE,
    )
    session.add(ticket)
    await session.flush()
    return ticket


async def create_knowledge(
    session: AsyncSession,
    question: str = "영업시간이 어떻게 되나요?",
    answer: str = "매일 10시부터 22시까지입니다.",
    ticket: SupportTicket | None = None,
    is_active: bool = True,
) -> KnowledgeEntry:
    entry = KnowledgeEntry(
        question=question,
        answer=answer,
        source_ticket_id=ticket.id if ticket else None,
        is_active=is_active,
    )
    session.add(entry)
    await session.flush()
    return entry

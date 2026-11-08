from datetime import datetime, time

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BusinessHour,
    Conversation,
    EscalationReason,
    KnowledgeEntry,
    KnowledgeLifetime,
    Message,
    MessageRole,
    Store,
    SupportTicket,
)


async def create_store(
    session: AsyncSession,
    slug: str = "test-store",
    name: str = "Test Store",
    timezone: str = "America/Los_Angeles",
) -> Store:
    store = Store(slug=slug, name=name, timezone=timezone)
    session.add(store)
    await session.flush()
    return store


async def create_business_hour(
    session: AsyncSession,
    store: Store,
    day_of_week: int,
    open_time: time | None = time(9, 0),
    close_time: time | None = time(18, 0),
) -> BusinessHour:
    hour = BusinessHour(store_id=store.id, day_of_week=day_of_week, open_time=open_time, close_time=close_time)
    session.add(hour)
    await session.flush()
    return hour


async def create_conversation(session: AsyncSession, store: Store, visitor_id: str = "visitor-1") -> Conversation:
    conversation = Conversation(store_id=store.id, visitor_id=visitor_id)
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
    knowledge_lifetime: KnowledgeLifetime = KnowledgeLifetime.NONE,
) -> SupportTicket:
    # 티켓의 매장은 항상 대화의 매장과 같다. 서비스도 이렇게 부모에서 복사해서 채운다.
    ticket = SupportTicket(
        store_id=conversation.store_id,
        conversation_id=conversation.id,
        source_message_id=source_message.id if source_message else None,
        question=question,
        escalation_reason=EscalationReason.NO_KNOWLEDGE,
        knowledge_lifetime=knowledge_lifetime,
    )
    session.add(ticket)
    await session.flush()
    return ticket


async def create_knowledge(
    session: AsyncSession,
    store: Store,
    question: str = "영업시간이 어떻게 되나요?",
    answer: str = "매일 10시부터 22시까지입니다.",
    ticket: SupportTicket | None = None,
    is_active: bool = True,
    expires_at: datetime | None = None,
) -> KnowledgeEntry:
    entry = KnowledgeEntry(
        store_id=store.id,
        question=question,
        answer=answer,
        source_ticket_id=ticket.id if ticket else None,
        is_active=is_active,
        expires_at=expires_at,
    )
    session.add(entry)
    await session.flush()
    return entry

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    MetaData,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Alembic이 제약 이름을 안정적으로 만들 수 있도록 이름 규칙을 고정한다.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # datetime은 항상 timestamptz로 저장한다. PostgreSQL은 내부적으로 UTC로 보관한다.
    type_annotation_map = {datetime: DateTime(timezone=True)}


class ConversationStatus(enum.StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


class MessageRole(enum.StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class EscalationReason(enum.StrEnum):
    NO_KNOWLEDGE = "no_knowledge"
    INSUFFICIENT_KNOWLEDGE = "insufficient_knowledge"


class TicketStatus(enum.StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


def _pg_enum(enum_class: type[enum.StrEnum], name: str) -> Enum:
    """멤버 이름(ACTIVE)이 아니라 값(active)을 DB enum 레이블로 저장한다."""
    return Enum(enum_class, name=name, values_callable=lambda members: [m.value for m in members])


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_visitor_id_created_at", "visitor_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    # 로그인이 없으므로 브라우저가 만든 익명 방문자 식별자를 그대로 저장한다.
    visitor_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[ConversationStatus] = mapped_column(
        _pg_enum(ConversationStatus, "conversation_status"),
        default=ConversationStatus.ACTIVE,
        server_default=ConversationStatus.ACTIVE.value,
    )
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, server_default=func.now())

    # async에서는 lazy loading이 불가능하므로 암묵적 로딩은 에러로 막고 selectinload로 명시 로드한다.
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
        order_by="Message.created_at",
    )
    tickets: Mapped[list["SupportTicket"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
        order_by="SupportTicket.created_at",
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_id_created_at", "conversation_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    role: Mapped[MessageRole] = mapped_column(_pg_enum(MessageRole, "message_role"))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())

    conversation: Mapped[Conversation] = relationship(back_populates="messages", lazy="raise")


class SupportTicket(Base):
    __tablename__ = "support_tickets"
    __table_args__ = (
        # 사장 문의 목록: 상태별로 오래된 순 조회
        Index("ix_support_tickets_status_created_at", "status", "created_at"),
        Index("ix_support_tickets_conversation_id", "conversation_id"),
        # 같은 고객 메시지에서 티켓이 두 번 생성되는 것을 막는다. NULL은 여러 건 허용된다.
        Index("uq_support_tickets_source_message_id", "source_message_id", unique=True),
        CheckConstraint(
            "status = 'open' OR (resolved_at IS NOT NULL AND owner_answer IS NOT NULL)",
            name="resolved_requires_answer",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    # 메시지가 정리되어도 사장이 답한 티켓 기록은 남도록 SET NULL로 둔다.
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    question: Mapped[str] = mapped_column(Text)
    escalation_reason: Mapped[EscalationReason] = mapped_column(_pg_enum(EscalationReason, "escalation_reason"))
    owner_answer: Mapped[str | None] = mapped_column(Text)
    status: Mapped[TicketStatus] = mapped_column(
        _pg_enum(TicketStatus, "ticket_status"),
        default=TicketStatus.OPEN,
        server_default=TicketStatus.OPEN.value,
    )
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column()

    conversation: Mapped[Conversation] = relationship(back_populates="tickets", lazy="raise")
    source_message: Mapped[Message | None] = relationship(lazy="raise")
    knowledge_entry: Mapped["KnowledgeEntry | None"] = relationship(back_populates="source_ticket", lazy="raise")


class KnowledgeEntry(Base):
    __tablename__ = "knowledge_entries"
    __table_args__ = (
        # 공백과 대소문자만 다른 같은 질문이 활성 상태로 중복 등록되는 것을 막는다.
        # 비활성화된 지식은 제외되므로 같은 질문을 새로 등록할 수 있다.
        Index(
            "uq_knowledge_entries_active_question",
            text(r"lower(regexp_replace(btrim(question), '\s+', ' ', 'g'))"),
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    question: Mapped[str] = mapped_column(String(500))
    answer: Mapped[str] = mapped_column(Text)
    # unique: 티켓 하나에서 지식이 두 개 만들어지지 않는다(0..1:1). 티켓 없이 만든 지식은 NULL.
    source_ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("support_tickets.id", ondelete="SET NULL"), unique=True
    )
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, server_default=func.now())

    source_ticket: Mapped[SupportTicket | None] = relationship(back_populates="knowledge_entry", lazy="raise")

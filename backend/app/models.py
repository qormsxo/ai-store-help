import enum
import uuid
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, validates

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


class KnowledgeLifetime(enum.StrEnum):
    """사장이 답을 등록할 때 그 답을 지식으로 어떻게 둘지 고른다."""

    NONE = "none"
    PERMANENT = "permanent"
    # 사장이 고른 날짜가 끝나는 매장 현지 다음 날 0시에 만료된다.
    UNTIL = "until"


def _pg_enum(enum_class: type[enum.StrEnum], name: str) -> Enum:
    """멤버 이름(ACTIVE)이 아니라 값(active)을 DB enum 레이블로 저장한다."""
    return Enum(enum_class, name=name, values_callable=lambda members: [m.value for m in members])


class Store(Base):
    """이 서비스가 응대하는 매장. 한 실행 환경에서는 DEFAULT_STORE_SLUG의 매장 하나만 쓴다."""

    __tablename__ = "stores"
    __table_args__ = (
        CheckConstraint(r"slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name="slug_format"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(String(300))
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(254))
    website: Mapped[str | None] = mapped_column(String(255))
    # IANA 이름(예: America/Los_Angeles). BusinessHour의 시각은 이 타임존 기준 현지 시각이다.
    timezone: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, server_default=func.now())

    business_hours: Mapped[list["BusinessHour"]] = relationship(
        back_populates="store",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
        order_by="BusinessHour.day_of_week",
    )
    # 자식 데이터가 있는 매장은 삭제할 수 없다(FK RESTRICT). ORM이 store_id를 NULL로 바꾸려 하지 못하게 "all".
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="store", passive_deletes="all", lazy="raise"
    )
    tickets: Mapped[list["SupportTicket"]] = relationship(back_populates="store", passive_deletes="all", lazy="raise")
    knowledge_entries: Mapped[list["KnowledgeEntry"]] = relationship(
        back_populates="store", passive_deletes="all", lazy="raise"
    )

    @validates("timezone")
    def _validate_timezone(self, _: str, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"올바른 IANA 타임존이 아닙니다: {value!r}") from exc
        return value


class BusinessHour(Base):
    """요일별 영업시간. open_time/close_time이 모두 NULL이면 그 요일은 휴무다."""

    __tablename__ = "business_hours"
    __table_args__ = (
        UniqueConstraint("store_id", "day_of_week", name="uq_business_hours_store_id_day_of_week"),
        CheckConstraint("day_of_week BETWEEN 0 AND 6", name="day_of_week_range"),
        CheckConstraint("(open_time IS NULL) = (close_time IS NULL)", name="open_close_both_or_neither"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"))
    # 0=월요일 ... 6=일요일 (date.weekday()와 같은 기준)
    day_of_week: Mapped[int]
    # close_time <= open_time이면 자정을 넘기는 영업(예: 18:00~02:00)으로 해석한다.
    open_time: Mapped[time | None]
    close_time: Mapped[time | None]

    store: Mapped[Store] = relationship(back_populates="business_hours", lazy="raise")

    @property
    def is_closed(self) -> bool:
        return self.open_time is None


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_store_id_visitor_id_created_at", "store_id", "visitor_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id", ondelete="RESTRICT"))
    # 로그인이 없으므로 브라우저가 만든 익명 방문자 식별자를 그대로 저장한다.
    visitor_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, server_default=func.now())

    store: Mapped[Store] = relationship(back_populates="conversations", lazy="raise")
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
        # 사장 문의 목록: 매장의 상태별 문의를 오래된 순으로 조회
        Index("ix_support_tickets_store_id_status_created_at", "store_id", "status", "created_at"),
        Index("ix_support_tickets_conversation_id", "conversation_id"),
        # 같은 고객 메시지에서 티켓이 두 번 생성되는 것을 막는다. NULL은 여러 건 허용된다.
        Index("uq_support_tickets_source_message_id", "source_message_id", unique=True),
        CheckConstraint(
            "status = 'open' OR (resolved_at IS NOT NULL AND owner_answer IS NOT NULL)",
            name="resolved_requires_answer",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    # 값은 항상 conversation.store_id와 같아야 한다. 서비스가 대화에서 복사해서 채운다.
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id", ondelete="RESTRICT"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    # 메시지가 정리되어도 사장이 답한 티켓 기록은 남도록 SET NULL로 둔다.
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    question: Mapped[str] = mapped_column(Text)
    escalation_reason: Mapped[EscalationReason] = mapped_column(_pg_enum(EscalationReason, "escalation_reason"))
    # 답변 전에는 none이다. 사장이 답을 등록할 때 고른 값으로 바뀌고, 그 값으로 지식을 남긴다.
    knowledge_lifetime: Mapped[KnowledgeLifetime] = mapped_column(
        _pg_enum(KnowledgeLifetime, "knowledge_lifetime"),
        default=KnowledgeLifetime.NONE,
        server_default=KnowledgeLifetime.NONE.value,
    )
    owner_answer: Mapped[str | None] = mapped_column(Text)
    status: Mapped[TicketStatus] = mapped_column(
        _pg_enum(TicketStatus, "ticket_status"),
        default=TicketStatus.OPEN,
        server_default=TicketStatus.OPEN.value,
    )
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column()

    store: Mapped[Store] = relationship(back_populates="tickets", lazy="raise")
    conversation: Mapped[Conversation] = relationship(back_populates="tickets", lazy="raise")
    source_message: Mapped[Message | None] = relationship(lazy="raise")
    knowledge_entry: Mapped["KnowledgeEntry | None"] = relationship(back_populates="source_ticket", lazy="raise")


class KnowledgeEntry(Base):
    __tablename__ = "knowledge_entries"
    __table_args__ = (
        # 한 매장 안에서 공백과 대소문자만 다른 같은 질문이 활성 상태로 중복 등록되는 것을 막는다.
        # 비활성화된 지식은 제외되므로 같은 질문을 새로 등록할 수 있고, 다른 매장과는 겹쳐도 된다.
        Index(
            "uq_knowledge_entries_store_active_question",
            "store_id",
            text(r"lower(regexp_replace(btrim(question), '\s+', ' ', 'g'))"),
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    # 출처 티켓이 있으면 ticket.store_id와 같아야 한다. 서비스가 티켓에서 복사해서 채운다.
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id", ondelete="RESTRICT"))
    question: Mapped[str] = mapped_column(String(500))
    answer: Mapped[str] = mapped_column(Text)
    # unique: 티켓 하나에서 지식이 두 개 만들어지지 않는다(0..1:1). 티켓 없이 만든 지식은 NULL.
    source_ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("support_tickets.id", ondelete="SET NULL"), unique=True
    )
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    # NULL이면 기한 없이 쓴다. 값이 있으면 그 시각부터 검색과 재사용에서 빠진다.
    expires_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, server_default=func.now())

    store: Mapped[Store] = relationship(back_populates="knowledge_entries", lazy="raise")
    source_ticket: Mapped[SupportTicket | None] = relationship(back_populates="knowledge_entry", lazy="raise")

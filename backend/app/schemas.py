import uuid
from datetime import datetime, time
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.models import EscalationReason, KnowledgeLifetime, MessageRole, TicketStatus


class HealthResponse(BaseModel):
    status: str
    database: str


class BusinessHourResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # 0=월요일 ... 6=일요일. 시각은 매장 timezone 기준 현지 시각이다.
    day_of_week: int
    open_time: time | None
    close_time: time | None
    is_closed: bool


class StoreResponse(BaseModel):
    """고객에게 보여줄 매장 정보. 내부 id는 노출하지 않는다."""

    model_config = ConfigDict(from_attributes=True)

    slug: str
    name: str
    description: str | None
    address: str | None
    phone: str | None
    email: str | None
    website: str | None
    timezone: str
    business_hours: list[BusinessHourResponse]


MAX_MESSAGE_LENGTH = 2000


class ConversationCreate(BaseModel):
    # 브라우저가 만든 익명 방문자 식별자. 인증 수단이 아니다.
    visitor_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class ConversationSummaryResponse(BaseModel):
    """대화 목록의 한 줄. 마지막 고객/AI 메시지 앞부분만 담는다."""

    id: uuid.UUID
    visitor_id: str
    created_at: datetime
    last_message: str | None


class ConversationResponse(BaseModel):
    """고객에게 보여줄 대화 정보. 매장 id는 노출하지 않는다."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    visitor_id: str
    created_at: datetime


class MessageCreate(BaseModel):
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MESSAGE_LENGTH)]


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: MessageRole
    content: str
    created_at: datetime


class MessageDeltaEvent(BaseModel):
    """SSE message_delta: AI가 방금 생성한 텍스트 조각. 이어 붙이면 답변이 된다."""

    text: str


class CompletedEvent(BaseModel):
    """SSE completed: 답변 전체가 Assistant 메시지 하나로 저장되었다."""

    message_id: uuid.UUID


class StreamErrorEvent(BaseModel):
    """SSE error: 스트림 도중 실패. 일반 오류 응답과 같은 code와 안전한 message만 담는다."""

    code: str
    message: str


MAX_ANSWER_LENGTH = 2000


class TicketResponse(BaseModel):
    """사장에게 보여줄 문의 한 건."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: TicketStatus
    # AI가 고객 메시지를 사장에게 보내기 좋게 다시 쓴 질문. 지식으로 저장될 때 질문이 된다.
    question: str
    escalation_reason: EscalationReason
    knowledge_lifetime: KnowledgeLifetime
    conversation_id: uuid.UUID
    owner_answer: str | None
    created_at: datetime
    resolved_at: datetime | None


class TicketConversationResponse(ConversationResponse):
    messages: list[MessageResponse]


class TicketDetailResponse(TicketResponse):
    # 문의가 만들어진 고객 메시지. 메시지가 정리되어 없을 수 있다.
    source_message_id: uuid.UUID | None
    conversation: TicketConversationResponse


class OwnerReplyCreate(BaseModel):
    answer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_ANSWER_LENGTH)]
    # 사장이 답변 등록 때 고른다. 티켓에 미리 적힌 값은 쓰지 않는다.
    knowledge_lifetime: KnowledgeLifetime


class OwnerReplyResponse(BaseModel):
    ticket: TicketResponse
    # 티켓이 지식을 남기지 않으면 null이다.
    knowledge_entry_id: uuid.UUID | None
    # 같은 질문의 지식이 이미 있어서 이번 답변으로 대체(비활성화)된 지식. 없으면 null.
    superseded_knowledge_id: uuid.UUID | None
    # 고객 대화에 추가된 답변 메시지
    conversation_message: MessageResponse


class StoreInfoItem(BaseModel):
    """매장 기본 정보. content에는 DB에 값이 있는 항목만 들어간다."""

    source_type: Literal["store"] = "store"
    source_id: uuid.UUID
    content: str


class BusinessHourItem(BaseModel):
    """요일 하나의 영업시간. DB에 행이 있는 요일만 만든다."""

    source_type: Literal["business_hour"] = "business_hour"
    source_id: uuid.UUID
    content: str


class StoreProfile(BaseModel):
    store: StoreInfoItem
    business_hours: list[BusinessHourItem]


class KnowledgeSearchItem(BaseModel):
    """Knowledge 검색 결과 하나.

    결과가 있다는 것은 질문과 관련된 후보를 찾았다는 뜻이지, 고객 질문에 답할 수 있다는 보장이 아니다.
    relevance_score는 결과끼리 순위를 비교하는 값이고 답변 가능 여부의 기준(임계값)이 아니다.
    """

    source_type: Literal["knowledge"] = "knowledge"
    source_id: uuid.UUID
    question: str
    answer: str
    relevance_score: float


class ErrorDetail(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody

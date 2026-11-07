import uuid
from datetime import time
from typing import Literal

from pydantic import BaseModel, ConfigDict


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

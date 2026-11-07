from datetime import time

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

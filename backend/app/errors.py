import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas import ErrorBody, ErrorDetail, ErrorResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    """도메인 예외의 기반 클래스. 모든 에러 응답은 {"error": {"code", "message"}} 형식이다."""

    status_code = 500
    code = "INTERNAL_ERROR"

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.code
        super().__init__(self.message)


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"


class StoreNotConfiguredError(AppError):
    """DEFAULT_STORE_SLUG에 해당하는 매장이 DB에 없다. seed 스크립트를 실행하지 않았을 때 발생한다."""

    status_code = 503
    code = "STORE_NOT_CONFIGURED"


class ConversationNotFoundError(NotFoundError):
    code = "CONVERSATION_NOT_FOUND"


class ConversationClosedError(ConflictError):
    code = "CONVERSATION_CLOSED"


class TicketNotFoundError(NotFoundError):
    code = "TICKET_NOT_FOUND"


class TicketAlreadyResolvedError(ConflictError):
    code = "TICKET_ALREADY_RESOLVED"


class LLMNotConfiguredError(AppError):
    """GEMINI_API_KEY/GEMINI_MODEL이 없거나, 키 또는 모델이 Gemini에서 거부되었다."""

    status_code = 503
    code = "LLM_NOT_CONFIGURED"


class LLMTimeoutError(AppError):
    status_code = 504
    code = "LLM_TIMEOUT"


class LLMRateLimitedError(AppError):
    status_code = 429
    code = "LLM_RATE_LIMITED"


class LLMUnavailableError(AppError):
    """재시도해도 Gemini 서버 오류나 네트워크 오류가 계속되었다."""

    status_code = 503
    code = "LLM_UNAVAILABLE"


class LLMRequestError(AppError):
    """Gemini가 요청 자체를 거부했다(재시도해도 같은 결과가 나오는 4xx)."""

    status_code = 502
    code = "LLM_REQUEST_FAILED"


class MessageStorageError(AppError):
    """메시지를 DB에 저장하지 못했다. 고객은 같은 메시지를 다시 보내면 된다."""

    status_code = 503
    code = "MESSAGE_SAVE_FAILED"


class AgentLimitExceededError(AppError):
    status_code = 502
    code = "AGENT_LIMIT_EXCEEDED"


class AgentEmptyResponseError(AppError):
    """LLM이 Tool 호출도 최종 텍스트도 돌려주지 않았다."""

    status_code = 502
    code = "AGENT_EMPTY_RESPONSE"


def _error_response(
    status_code: int,
    code: str,
    message: str,
    details: list[ErrorDetail] | None = None,
) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details))
    return JSONResponse(status_code=status_code, content=body.model_dump(exclude_none=True))


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return _error_response(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [ErrorDetail(loc=list(e["loc"]), msg=e["msg"], type=e["type"]) for e in exc.errors()]
        return _error_response(422, "VALIDATION_ERROR", "요청 값이 올바르지 않습니다.", details)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "NOT_FOUND" if exc.status_code == 404 else f"HTTP_{exc.status_code}"
        return _error_response(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        # 스택은 로그에만 남기고 클라이언트에는 내부 정보를 노출하지 않는다.
        logger.exception("Unhandled exception", exc_info=exc)
        return _error_response(500, "INTERNAL_ERROR", "서버 내부 오류가 발생했습니다.")

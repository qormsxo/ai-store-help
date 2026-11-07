import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

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


def _error_response(status_code: int, code: str, message: str, details: object | None = None) -> JSONResponse:
    error: dict[str, object] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse(status_code=status_code, content={"error": error})


async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
    return _error_response(exc.status_code, exc.code, exc.message)


async def _handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return _error_response(422, "VALIDATION_ERROR", "요청 값이 올바르지 않습니다.", jsonable_details(exc))


def jsonable_details(exc: RequestValidationError) -> list[dict[str, object]]:
    return [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]


async def _handle_http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = "NOT_FOUND" if exc.status_code == 404 else f"HTTP_{exc.status_code}"
    return _error_response(exc.status_code, code, str(exc.detail))


async def _handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    # 스택은 로그에만 남기고 클라이언트에는 내부 정보를 노출하지 않는다.
    logger.exception("Unhandled exception", exc_info=exc)
    return _error_response(500, "INTERNAL_ERROR", "서버 내부 오류가 발생했습니다.")


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _handle_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _handle_unexpected_error)

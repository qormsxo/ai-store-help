import asyncio
import logging
from collections.abc import AsyncGenerator

import anyio
from fastapi import Request
from pydantic import BaseModel

from app.errors import AppError
from app.llm.types import TextDelta
from app.schemas import CompletedEvent, MessageDeltaEvent, StreamErrorEvent
from app.services.reply_service import ReplyCompleted, ReplyStreamEvent

logger = logging.getLogger(__name__)

# 연결이 끊겼는지 확인하는 간격. 이벤트가 오지 않는 동안(Tool 실행, Gemini 대기)에도 끊김을 알아챈다.
_DISCONNECT_POLL_SECONDS = 0.25


def format_sse(event: str, data: BaseModel) -> str:
    """SSE 이벤트 한 건. data는 JSON 한 줄이라서 텍스트의 줄바꿈과 따옴표는 이스케이프되어 형식이 깨지지 않는다."""
    return f"event: {event}\ndata: {data.model_dump_json()}\n\n"


async def stream_chat_events(request: Request, events: AsyncGenerator[ReplyStreamEvent]) -> AsyncGenerator[str]:
    """채팅 이벤트를 SSE 문자열로 바꿔 내보낸다.

    - 실패는 error 이벤트로 알리고 끝낸다. 이미 200 응답이 시작되어 HTTP 상태 코드를 바꿀 수 없다.
      내용은 일반 오류 응답과 같은 code와 사용자용 message뿐이다. 스택 트레이스는 로그에만 남는다.
    - 고객 연결이 끊기면 진행 중인 Gemini 호출과 이후 작업을 취소한다. Starlette는 다음 조각을 보내려 할 때에야
      끊김을 알기 때문에, 이벤트를 기다리는 동안에는 직접 확인한다.
    """
    pending: asyncio.Task[ReplyStreamEvent | None] | None = None
    try:
        while True:
            pending = asyncio.create_task(_next_event(events))
            # 태스크를 실행하면서 (실제로 asyncio.wait() 에서 제너레이터가 실행 )고객 연결이 끊겼는지 확인
            while not (await asyncio.wait({pending}, timeout=_DISCONNECT_POLL_SECONDS))[0]:
                if await request.is_disconnected():
                    logger.info("Client disconnected while streaming the reply")
                    return
            try:
                event = pending.result()
            except AppError as exc:
                yield format_sse("error", StreamErrorEvent(code=exc.code, message=exc.message))
                return
            except Exception:
                logger.exception("Unexpected error while streaming the reply")
                yield format_sse(
                    "error", StreamErrorEvent(code="INTERNAL_ERROR", message="서버 내부 오류가 발생했습니다.")
                )
                return
            match event:
                # none 인경우는 제너레이터가 끝났을때.
                case None:
                    return
                case TextDelta(text=text):
                    yield format_sse("message_delta", MessageDeltaEvent(text=text))
                case ReplyCompleted(assistant_message=message):
                    yield format_sse("completed", CompletedEvent(message_id=message.id))
    finally:
        # 끝났든, 끊겼든, 취소됐든 진행 중인 작업을 멈추고 정리가 끝날 때까지 기다린다(DB 롤백, Gemini 연결 닫기).
        with anyio.CancelScope(shield=True):
            if pending is not None and not pending.done():
                pending.cancel()
            if pending is not None:
                await asyncio.gather(pending, return_exceptions=True)
            await events.aclose()


async def _next_event(events: AsyncGenerator[ReplyStreamEvent]) -> ReplyStreamEvent | None:
    return await anext(events, None)

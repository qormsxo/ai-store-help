"""진짜 GeminiProvider를 네트워크 없이 돌리기 위한 가짜 SDK 호출.

GeminiProvider가 `client.aio.models.generate_content` 대신 FakeGenerateContent를,
`generate_content_stream` 대신 FakeGenerateContentStream을 부른다.
요청(Content)과 응답(GenerateContentResponse), 오류(APIError)는 모두 진짜 google-genai 타입이다.
"""

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

from google.genai import errors, types

from app.llm.gemini import GeminiProvider


@dataclass(frozen=True)
class Stall:
    """응답 대신 이만큼 기다린다(timeout 테스트용)."""

    seconds: float


@dataclass(frozen=True)
class RecordedCall:
    model: str
    contents: list[types.Content]
    config: types.GenerateContentConfig


@dataclass
class FakeGenerateContent:
    outcomes: list[types.GenerateContentResponse | Exception | Stall]
    calls: list[RecordedCall] = field(default_factory=list)

    async def __call__(
        self, *, model: str, contents: list[types.Content], config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse:
        # 호출 시점의 복사본을 남긴다. Provider가 이후에 리스트를 바꿔도 기록은 변하지 않는다.
        self.calls.append(RecordedCall(model, list(contents), config))
        match self.outcomes.pop(0):
            case Stall(seconds=seconds):
                await asyncio.sleep(seconds)
                raise AssertionError("timeout이 먼저 나야 한다")
            case Exception() as error:
                raise error
            case types.GenerateContentResponse() as response:
                return response


type StreamStep = types.GenerateContentResponse | Exception | Stall


@dataclass
class FakeGenerateContentStream:
    """`generate_content_stream`의 가짜. 호출마다 streams에서 하나를 꺼내 청크(응답 조각)를 순서대로 내보낸다.

    stream이 Exception이면 스트림을 여는 호출이 실패한다. 리스트 안의 Exception은 그 지점의 청크 대신 발생하고,
    Stall은 다음 청크 전에 그만큼 멈춘다. closed는 스트림이 끝나거나 닫힌 횟수(연결을 닫았는지 확인용)다.
    """

    streams: list[list[StreamStep] | Exception]
    calls: list[RecordedCall] = field(default_factory=list)
    closed: int = 0

    async def __call__(
        self, *, model: str, contents: list[types.Content], config: types.GenerateContentConfig
    ) -> AsyncGenerator[types.GenerateContentResponse]:
        self.calls.append(RecordedCall(model, list(contents), config))
        match self.streams.pop(0):
            case Exception() as error:
                raise error
            case list() as steps:
                return self._chunks(steps)
        raise AssertionError("스트림 스크립트가 올바르지 않습니다.")

    async def _chunks(self, steps: list[StreamStep]) -> AsyncGenerator[types.GenerateContentResponse]:
        try:
            for step in steps:
                match step:
                    case Stall(seconds=seconds):
                        await asyncio.sleep(seconds)
                    case Exception() as error:
                        raise error
                    case types.GenerateContentResponse() as chunk:
                        yield chunk
        finally:
            self.closed += 1


def provider_with(
    fake: FakeGenerateContent,
    stream: FakeGenerateContentStream | None = None,
    *,
    timeout_seconds: float = 5,
    max_attempts: int = 3,
) -> GeminiProvider:
    return GeminiProvider(
        fake,
        generate_content_stream=stream or FakeGenerateContentStream([]),
        model="gemini-test-model",
        max_output_tokens=1024,
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
        retry_base_delay=0,
    )


def response_with(
    *parts: types.Part, finish_reason: types.FinishReason = types.FinishReason.STOP
) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=list(parts)), finish_reason=finish_reason)]
    )


def function_call_response(name: str, call_id: str, **args: str) -> types.GenerateContentResponse:
    """Gemini가 Function Call 하나를 요청하는 응답(thought signature 포함)."""
    return response_with(
        types.Part(
            function_call=types.FunctionCall(id=call_id, name=name, args=dict(args)),
            thought_signature=f"signature-{call_id}".encode(),
        )
    )


def text_response(text: str) -> types.GenerateContentResponse:
    return response_with(types.Part(text=text))


def api_error(code: int, status: str, message: str = "error") -> errors.APIError:
    payload = {"error": {"code": code, "message": message, "status": status}}
    return errors.ServerError(code, payload) if code >= 500 else errors.ClientError(code, payload)


TEXT_RESPONSE = text_response("Hello!")

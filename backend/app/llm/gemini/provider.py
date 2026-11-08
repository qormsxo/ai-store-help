"""Gemini 호출, 재시도, 스트리밍. 도구 실행과 반복은 AgentService가 한다."""

import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import aclosing

import httpx2
from google.genai.errors import APIError
from google.genai.types import (
    AutomaticFunctionCallingConfig,
    Content,
    FunctionDeclaration,
    GenerateContentConfig,
    Tool,
)

from app.errors import LLMTimeoutError, LLMUnavailableError
from app.llm.gemini.convert import to_contents, to_declaration, to_model_turn, translate_api_error
from app.llm.gemini.protocols import GenerateContentStream, is_closable
from app.llm.types import ModelTurn, StreamItem, TextDelta, ToolCall, ToolDefinition, Turn

logger = logging.getLogger(__name__)

# 잠시 뒤 다시 보내면 성공할 수 있는 상태 코드만 재시도한다. 400/401/403/404는 몇 번을 보내도 같다.
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class GeminiProvider:
    """Gemini GenerateContent API 구현체. Function Calling은 수동 방식만 쓴다.

    SDK가 Python 함수를 직접 실행하는 Automatic Function Calling은 끄고, 모델이 요청한
    function_call을 공통 ToolCall로 돌려준다. 실행과 반복은 AgentService가 한다.
    generate_stream은 Gemini Streaming API로 텍스트 조각을 받는 대로 돌려준다.
    """

    def __init__(
        self,
        generate_content_stream: GenerateContentStream,
        *,
        model: str,
        max_output_tokens: int,
        timeout_seconds: float,
        close: Callable[[], Awaitable[None]] | None = None,
        max_attempts: int = 3,
        retry_base_delay: float = 1.0,
    ) -> None:
        """SDK 스트리밍 함수와 재시도 제한을 저장한다. create_gemini_provider가 만든다."""
        self._generate_content_stream = generate_content_stream
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._timeout_seconds = timeout_seconds
        self._close = close
        self._max_attempts = max_attempts
        self._retry_base_delay = retry_base_delay

    def _config(self, system_instruction: str, tools: Sequence[ToolDefinition]) -> GenerateContentConfig:
        """이번 호출의 Gemini 설정을 만든다. 도구 설명서는 FunctionDeclaration으로 바꾸고 자동 실행은 끈다."""
        declarations: list[FunctionDeclaration] = []
        for tool in tools:
            declarations.append(to_declaration(tool))
        gemini_tools: list[Tool] | None = None
        if declarations:
            gemini_tools = [Tool(function_declarations=declarations)]
        return GenerateContentConfig(
            system_instruction=system_instruction,
            tools=gemini_tools,
            max_output_tokens=self._max_output_tokens,
            automatic_function_calling=AutomaticFunctionCallingConfig(disable=True),
        )

    def _timeout_error(self) -> LLMTimeoutError:
        """제한 시간을 넘기면 LLMTimeoutError를 만든다. 재시도하지 않는다."""
        # 이미 제한 시간만큼 기다렸으므로 재시도하지 않는다. 재시도하면 고객 대기 시간이 배로 늘어난다.
        logger.warning("Gemini request timed out after %.1fs", self._timeout_seconds)
        return LLMTimeoutError("AI 응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.")

    def _retryable_api_failure(self, exc: APIError, attempt: int, *, can_retry: bool = True) -> str:
        """HTTP 오류가 재시도 대상이면 로그용 설명을 돌려주고, 아니면 우리 예외를 던진다."""
        if not can_retry or exc.code not in _RETRYABLE_STATUS_CODES or attempt >= self._max_attempts:
            raise translate_api_error(exc) from exc
        return f"HTTP {exc.code}"

    def _retryable_transport_failure(self, exc: httpx2.TransportError, attempt: int, *, can_retry: bool = True) -> str:
        """연결 오류에 HTTP 오류와 같은 3번 제한을 적용한다. 소진하면 LLMUnavailableError를 던진다."""
        if not can_retry or attempt >= self._max_attempts:
            logger.error("Gemini request failed: %s", type(exc).__name__)
            raise LLMUnavailableError("AI 서비스에 연결할 수 없습니다.") from exc
        return type(exc).__name__

    async def _backoff(self, failure: str, attempt: int) -> None:
        """재시도 전에 기다린다. 1회 실패 후 1초, 2회 실패 후 2초."""
        delay = self._retry_base_delay * 2 ** (attempt - 1)
        logger.warning(
            "Gemini request failed (%s), retrying in %.1fs (attempt %d/%d)",
            failure,
            delay,
            attempt,
            self._max_attempts,
        )
        await asyncio.sleep(delay)

    async def aclose(self) -> None:
        """저장해 둔 SDK 클라이언트를 닫는다. 앱 종료 때 호출한다."""
        if self._close is not None:
            await self._close()

    async def generate_stream(
        self, *, system_instruction: str, turns: Sequence[Turn], tools: Sequence[ToolDefinition]
    ) -> AsyncGenerator[StreamItem]:
        """채팅이 쓰는 호출 한 번. TextDelta를 먼저 내고, 마지막에 ModelTurn 하나를 낸다.

        재시도는 첫 TextDelta를 내보내기 전에만 한다. 이미 고객 화면에 나간 글자를 다시 받으면 중복되므로,
        텍스트를 내보낸 뒤의 오류는 그대로 올린다. Function Call만 받은 상태의 오류는 아직 내보낸 것이 없어서 재시도한다.
        """
        config = self._config(system_instruction, tools)
        contents = to_contents(turns)
        attempt = 1
        while True:
            delivered_text = False
            try:
                async with aclosing(self._stream_once(contents, config)) as items:
                    async for item in items:
                        match item:
                            case TextDelta():
                                delivered_text = True
                                yield item
                            case ModelTurn():
                                yield item
                return
            except (TimeoutError, httpx2.TimeoutException) as exc:
                raise self._timeout_error() from exc
            except APIError as exc:
                # 글자를 화면에 보낸 뒤에는 재시도하지 않는다.
                failure = self._retryable_api_failure(exc, attempt, can_retry=not delivered_text)
            except httpx2.TransportError as exc:
                # 연결 오류도 글자를 보낸 뒤에는 재시도하지 않는다.
                failure = self._retryable_transport_failure(exc, attempt, can_retry=not delivered_text)
            # 재시도 전 대기 시간을 준다.
            await self._backoff(failure, attempt)
            attempt += 1

    async def _stream_once(
        self, contents: list[Content], config: GenerateContentConfig
    ) -> AsyncGenerator[StreamItem]:
        """스트림 한 번을 읽어 TextDelta와 마지막 ModelTurn으로 나눈다.

        제한 시간은 응답 전체가 아니라 "다음 조각이 올 때까지"에 적용한다.
        답변이 길어도 조각이 계속 오면 끊지 않고, 조각이 멈추면 제한 시간 뒤에 끊는다.
        제너레이터가 중간에 닫히면 finally가 Gemini 응답 연결도 같이 닫는다.
        """
        texts: list[str] = []
        tool_calls: list[ToolCall] = []
        finish_reason: str | None = None
        async with asyncio.timeout(self._timeout_seconds):
            stream = await self._generate_content_stream(model=self._model, contents=contents, config=config)
        try:
            chunks = aiter(stream)
            while True:
                try:
                    async with asyncio.timeout(self._timeout_seconds):
                        chunk = await anext(chunks)
                except StopAsyncIteration:
                    break
                part = to_model_turn(chunk)
                tool_calls += part.tool_calls
                finish_reason = part.finish_reason or finish_reason
                if part.text:
                    texts.append(part.text)
                    yield TextDelta(text=part.text)
        finally:
            if is_closable(stream):
                await stream.aclose()
        yield ModelTurn(text="".join(texts) or None, tool_calls=tool_calls, finish_reason=finish_reason)

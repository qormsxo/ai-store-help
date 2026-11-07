import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol, assert_never

import httpx2
from google import genai
from google.genai import errors, types
from pydantic import JsonValue, TypeAdapter

from app.config import Settings
from app.errors import (
    AppError,
    LLMNotConfiguredError,
    LLMRateLimitedError,
    LLMRequestError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.llm.types import ModelTurn, ToolCall, ToolDefinition, ToolResultTurn, Turn, UserTurn

logger = logging.getLogger(__name__)

# 잠시 뒤 다시 보내면 성공할 수 있는 상태 코드만 재시도한다. 400/401/403/404는 몇 번을 보내도 같다.
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_ARGUMENTS = TypeAdapter(dict[str, JsonValue])


class GenerateContent(Protocol):
    """`client.aio.models.generate_content`의 필요한 부분. 테스트는 가짜 구현을 넣는다."""

    async def __call__(
        self, *, model: str, contents: list[types.Content], config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse: ...


class GeminiProvider:
    """Gemini GenerateContent API 구현체. Function Calling은 수동 방식만 쓴다.

    SDK가 Python 함수를 직접 실행하는 Automatic Function Calling은 끄고, 모델이 요청한
    function_call을 공통 ToolCall로 돌려준다. 실행과 반복은 AgentService가 한다.
    """

    def __init__(
        self,
        generate_content: GenerateContent,
        *,
        model: str,
        max_output_tokens: int,
        timeout_seconds: float,
        close: Callable[[], Awaitable[None]] | None = None,
        max_attempts: int = 3,
        retry_base_delay: float = 1.0,
    ) -> None:
        self._generate_content = generate_content
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._timeout_seconds = timeout_seconds
        self._close = close
        self._max_attempts = max_attempts
        self._retry_base_delay = retry_base_delay

    async def generate(
        self, *, system_instruction: str, turns: Sequence[Turn], tools: Sequence[ToolDefinition]
    ) -> ModelTurn:
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=[types.Tool(function_declarations=[_to_declaration(tool) for tool in tools])] if tools else None,
            max_output_tokens=self._max_output_tokens,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        response = await self._generate_with_retry([_to_content(turn) for turn in turns], config)
        return _to_model_turn(response)

    async def aclose(self) -> None:
        if self._close is not None:
            await self._close()

    async def _generate_with_retry(
        self, contents: list[types.Content], config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse:
        attempt = 1
        while True:
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    return await self._generate_content(model=self._model, contents=contents, config=config)
            # google-genai 3.x의 전송 계층은 httpx2다(httpx가 아니다).
            except (TimeoutError, httpx2.TimeoutException) as exc:
                # 이미 제한 시간만큼 기다렸으므로 재시도하지 않는다. 재시도하면 고객 대기 시간이 배로 늘어난다.
                logger.warning("Gemini request timed out after %.1fs", self._timeout_seconds)
                raise LLMTimeoutError("AI 응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.") from exc
            except errors.APIError as exc:
                if exc.code not in _RETRYABLE_STATUS_CODES or attempt >= self._max_attempts:
                    raise _translate_api_error(exc) from exc
                failure = f"HTTP {exc.code}"
            except httpx2.TransportError as exc:
                if attempt >= self._max_attempts:
                    logger.error("Gemini request failed: %s", type(exc).__name__)
                    raise LLMUnavailableError("AI 서비스에 연결할 수 없습니다.") from exc
                failure = type(exc).__name__

            delay = self._retry_base_delay * 2 ** (attempt - 1)
            logger.warning(
                "Gemini request failed (%s), retrying in %.1fs (attempt %d/%d)",
                failure,
                delay,
                attempt,
                self._max_attempts,
            )
            await asyncio.sleep(delay)
            attempt += 1


def create_gemini_provider(settings: Settings) -> GeminiProvider:
    """환경변수 설정으로 Gemini 비동기 클라이언트를 만든다. 키나 모델이 없으면 LLMNotConfiguredError."""
    api_key = settings.gemini_api_key.get_secret_value().strip() if settings.gemini_api_key else ""
    if not api_key:
        raise LLMNotConfiguredError("GEMINI_API_KEY가 설정되지 않았습니다.")
    model = (settings.gemini_model or "").strip()
    if not model:
        raise LLMNotConfiguredError("GEMINI_MODEL이 설정되지 않았습니다.")

    client = genai.Client(
        api_key=api_key,
        # SDK 자체 재시도는 쓰지 않는다(기본값도 재시도 없음). 재시도는 GeminiProvider가 정한 규칙으로만 한다.
        http_options=types.HttpOptions(timeout=int(settings.gemini_timeout * 1000)),
    )
    return GeminiProvider(
        client.aio.models.generate_content,
        model=model,
        max_output_tokens=settings.gemini_max_output_tokens,
        timeout_seconds=settings.gemini_timeout,
        close=client.aio.aclose,
    )


def _translate_api_error(exc: errors.APIError) -> AppError:
    # 응답 본문에는 API 키가 들어 있지 않다. 상태 코드와 Google의 오류 메시지만 남긴다.
    logger.error("Gemini API error %s %s: %s", exc.code, exc.status, exc.message)
    if exc.code == 429:
        return LLMRateLimitedError("AI 요청 한도를 초과했습니다. 잠시 후 다시 시도해 주세요.")
    # Gemini는 잘못된 API 키를 400 INVALID_ARGUMENT로 돌려준다.
    if exc.code in (401, 403) or "API key" in (exc.message or ""):
        return LLMNotConfiguredError("Gemini API 키가 유효하지 않거나 권한이 없습니다.")
    if exc.code == 404:
        return LLMNotConfiguredError("GEMINI_MODEL에 설정한 모델을 찾을 수 없습니다.")
    if exc.code >= 500:
        return LLMUnavailableError("AI 서비스에 일시적인 문제가 있습니다. 잠시 후 다시 시도해 주세요.")
    return LLMRequestError("AI 요청이 거부되었습니다.")


def _to_declaration(tool: ToolDefinition) -> types.FunctionDeclaration:
    return types.FunctionDeclaration(
        name=tool.name, description=tool.description, parameters_json_schema=tool.parameters
    )


def _to_content(turn: Turn) -> types.Content:
    match turn:
        case UserTurn(text=text):
            return types.Content(role="user", parts=[types.Part(text=text)])
        case ModelTurn(text=text, tool_calls=tool_calls):
            parts = [types.Part(text=text)] if text else []
            parts += [
                types.Part(
                    function_call=types.FunctionCall(id=call.id, name=call.name, args=call.arguments),
                    thought_signature=call.signature,
                )
                for call in tool_calls
            ]
            return types.Content(role="model", parts=parts)
        case ToolResultTurn(results=results):
            # Gemini 형식: function_response는 user 역할로, 호출과 같은 id와 name을 붙여 보낸다.
            return types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id=result.call_id, name=result.name, response=result.content
                        )
                    )
                    for result in results
                ],
            )
        case _:
            assert_never(turn)


def _to_model_turn(response: types.GenerateContentResponse) -> ModelTurn:
    if not response.candidates:
        feedback = response.prompt_feedback
        reason = feedback.block_reason.value if feedback and feedback.block_reason else "NO_CANDIDATES"
        return ModelTurn(finish_reason=f"PROMPT_{reason}")

    candidate = response.candidates[0]
    parts = candidate.content.parts if candidate.content and candidate.content.parts else []
    texts = [part.text for part in parts if part.text and not part.thought]
    tool_calls = [
        ToolCall(
            id=part.function_call.id,
            name=part.function_call.name or "",
            arguments=_ARGUMENTS.validate_python(part.function_call.args or {}),
            signature=part.thought_signature,
        )
        for part in parts
        if part.function_call
    ]
    return ModelTurn(
        text="".join(texts) or None,
        tool_calls=tool_calls,
        finish_reason=candidate.finish_reason.value if candidate.finish_reason else None,
    )

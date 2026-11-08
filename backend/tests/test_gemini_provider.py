"""GeminiProvider 단위 테스트. 네트워크를 쓰지 않는다.

조각 순서와 요청 형식은 test_gemini_streaming.py가 본다. 여기는 대화 변환, 재시도 소진, 오류 매핑만 본다.
"""

import httpx2
import pytest
from google.genai import errors, types

from app.agent.tools import ToolDispatcher
from app.errors import (
    LLMNotConfiguredError,
    LLMRateLimitedError,
    LLMRequestError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.llm.gemini import GeminiProvider
from app.llm.types import ModelTurn, ToolCall, ToolResult, ToolResultTurn, UserTurn
from tests.fake_gemini import (
    TEXT_RESPONSE,
    FakeGenerateContentStream,
    Stall,
    api_error,
    as_single_chunk_streams,
    provider_with,
)


async def generate(provider: GeminiProvider, turns: list[UserTurn | ModelTurn | ToolResultTurn] | None = None) -> ModelTurn:
    """스트림의 마지막 ModelTurn을 돌려준다. 한 번에 받는 호출은 없다."""
    final: ModelTurn | None = None
    async for item in provider.generate_stream(
        system_instruction="system",
        turns=turns if turns is not None else [UserTurn(text="Hi")],
        tools=ToolDispatcher().definitions() if turns is None else [],
    ):
        match item:
            case ModelTurn() as turn:
                final = turn
    if final is None:
        raise AssertionError("ModelTurn이 없습니다.")
    return final


async def test_context_is_sent_in_gemini_function_calling_format() -> None:
    fake = as_single_chunk_streams([TEXT_RESPONSE])
    turns = [
        UserTurn(text="Do you allow dogs?"),
        ModelTurn(
            tool_calls=[ToolCall(id="call-1", name="search_knowledge", arguments={"query": "dogs"}, signature=b"sig")]
        ),
        ToolResultTurn(results=[ToolResult(call_id="call-1", name="search_knowledge", content={"results": []})]),
    ]

    await generate(provider_with(fake), turns)

    user, model, tool_result = fake.calls[0].contents
    assert (user.role, user.parts) == ("user", [types.Part(text="Do you allow dogs?")])
    assert model.role == "model"
    assert model.parts == [
        types.Part(
            function_call=types.FunctionCall(id="call-1", name="search_knowledge", args={"query": "dogs"}),
            # Gemini 3는 thought signature를 돌려보내지 않으면 요청을 거부한다.
            thought_signature=b"sig",
        )
    ]
    assert tool_result.role == "user"
    assert tool_result.parts == [
        types.Part(
            function_response=types.FunctionResponse(id="call-1", name="search_knowledge", response={"results": []})
        )
    ]
    assert fake.calls[0].config.tools is None


async def test_server_error_after_all_attempts_is_unavailable() -> None:
    fake = as_single_chunk_streams([api_error(503, "UNAVAILABLE")] * 3)

    with pytest.raises(LLMUnavailableError):
        await generate(provider_with(fake))

    assert len(fake.calls) == 3


async def test_rate_limit_after_all_attempts_is_rate_limited_error() -> None:
    fake = as_single_chunk_streams([api_error(429, "RESOURCE_EXHAUSTED")] * 3)

    with pytest.raises(LLMRateLimitedError) as exc_info:
        await generate(provider_with(fake))

    assert (exc_info.value.status_code, exc_info.value.code) == (429, "LLM_RATE_LIMITED")
    assert len(fake.calls) == 3


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (api_error(400, "INVALID_ARGUMENT", "Function call is missing a thought_signature"), LLMRequestError),
        (api_error(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key."), LLMNotConfiguredError),
        (api_error(403, "PERMISSION_DENIED"), LLMNotConfiguredError),
        (api_error(404, "NOT_FOUND", "models/gemini-typo is not found"), LLMNotConfiguredError),
    ],
)
async def test_client_errors_are_not_retried(error: errors.APIError, expected: type[Exception]) -> None:
    fake = as_single_chunk_streams([error, TEXT_RESPONSE])

    with pytest.raises(expected):
        await generate(provider_with(fake))

    assert len(fake.calls) == 1


async def test_slow_response_times_out_without_retry() -> None:
    fake = as_single_chunk_streams([Stall(seconds=5), TEXT_RESPONSE])

    with pytest.raises(LLMTimeoutError) as exc_info:
        await generate(provider_with(fake, timeout_seconds=0.05))

    assert (exc_info.value.status_code, exc_info.value.code) == (504, "LLM_TIMEOUT")
    assert len(fake.calls) == 1


async def test_sdk_http_timeout_is_timeout_error() -> None:
    # google-genai 3.x는 httpx2로 요청한다. SDK의 HttpOptions timeout이 먼저 끝나면 이 예외가 올라온다.
    fake = as_single_chunk_streams([httpx2.ReadTimeout("read timed out"), TEXT_RESPONSE])

    with pytest.raises(LLMTimeoutError):
        await generate(provider_with(fake))

    assert len(fake.calls) == 1


async def test_connection_error_is_retried_then_unavailable() -> None:
    fake = as_single_chunk_streams([httpx2.ConnectError("refused")] * 2)

    with pytest.raises(LLMUnavailableError):
        await generate(provider_with(fake, max_attempts=2))

    assert len(fake.calls) == 2


async def test_blocked_prompt_returns_empty_turn() -> None:
    fake = as_single_chunk_streams([
        types.GenerateContentResponse(
            prompt_feedback=types.GenerateContentResponsePromptFeedback(block_reason=types.BlockedReason.SAFETY)
        )
    ])

    turn = await generate(provider_with(fake))

    assert turn == ModelTurn(finish_reason="PROMPT_SAFETY")


async def test_aclose_closes_the_client() -> None:
    closed: list[bool] = []

    async def close() -> None:
        closed.append(True)

    provider = GeminiProvider(
        FakeGenerateContentStream([]),
        model="m",
        max_output_tokens=1,
        timeout_seconds=1,
        close=close,
    )
    await provider.aclose()

    assert closed == [True]

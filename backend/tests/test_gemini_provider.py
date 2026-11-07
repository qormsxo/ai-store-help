"""GeminiProvider 단위 테스트. 네트워크를 쓰지 않는다.

SDK의 generate_content 자리에 가짜 함수를 넣고, 진짜 google-genai 타입(요청 Content, 응답,
APIError)으로 변환, 재시도, 오류 매핑을 확인한다.
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
    FakeGenerateContent,
    FakeGenerateContentStream,
    Stall,
    api_error,
    provider_with,
    response_with,
)


async def generate(provider: GeminiProvider) -> ModelTurn:
    return await provider.generate(
        system_instruction="system", turns=[UserTurn(text="Hi")], tools=ToolDispatcher().definitions()
    )


async def test_function_call_response_becomes_tool_call() -> None:
    fake = FakeGenerateContent(
        [
            response_with(
                types.Part(text="Let me think about this.", thought=True),
                types.Part(
                    function_call=types.FunctionCall(id="call-1", name="search_knowledge", args={"query": "dogs"}),
                    thought_signature=b"signature-1",
                ),
            )
        ]
    )

    turn = await generate(provider_with(fake))

    # 생각(thought) 파트는 고객 답변 텍스트로 쓰지 않는다.
    assert turn.text is None
    assert turn.tool_calls == [
        ToolCall(id="call-1", name="search_knowledge", arguments={"query": "dogs"}, signature=b"signature-1")
    ]
    assert turn.finish_reason == "STOP"


async def test_text_response_becomes_final_text() -> None:
    turn = await generate(provider_with(FakeGenerateContent([TEXT_RESPONSE])))

    assert turn == ModelTurn(text="Hello!", finish_reason="STOP")


async def test_request_uses_manual_function_calling_and_settings() -> None:
    fake = FakeGenerateContent([TEXT_RESPONSE])

    await generate(provider_with(fake))

    call = fake.calls[0]
    assert call.model == "gemini-test-model"
    assert call.config.system_instruction == "system"
    assert call.config.max_output_tokens == 1024
    # SDK가 Python 함수를 직접 실행하지 않는다.
    assert call.config.automatic_function_calling == types.AutomaticFunctionCallingConfig(disable=True)
    assert call.config.tools is not None
    declarations = call.config.tools[0].function_declarations or []
    assert [d.name for d in declarations] == [
        "get_store_profile",
        "search_knowledge",
        "create_support_ticket",
        "reject_not_inquiry",
    ]
    assert declarations[0].parameters_json_schema is None
    search_schema = ToolDispatcher().definitions()[1].parameters
    assert declarations[1].parameters_json_schema == search_schema


async def test_context_is_sent_in_gemini_function_calling_format() -> None:
    fake = FakeGenerateContent([TEXT_RESPONSE])
    turns = [
        UserTurn(text="Do you allow dogs?"),
        ModelTurn(
            tool_calls=[ToolCall(id="call-1", name="search_knowledge", arguments={"query": "dogs"}, signature=b"sig")]
        ),
        ToolResultTurn(results=[ToolResult(call_id="call-1", name="search_knowledge", content={"results": []})]),
    ]

    await provider_with(fake).generate(system_instruction="system", turns=turns, tools=[])

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


async def test_server_error_is_retried_then_succeeds() -> None:
    fake = FakeGenerateContent([api_error(503, "UNAVAILABLE"), api_error(500, "INTERNAL"), TEXT_RESPONSE])

    turn = await generate(provider_with(fake))

    assert turn.text == "Hello!"
    assert len(fake.calls) == 3


async def test_server_error_after_all_attempts_is_unavailable() -> None:
    fake = FakeGenerateContent([api_error(503, "UNAVAILABLE")] * 3)

    with pytest.raises(LLMUnavailableError):
        await generate(provider_with(fake))

    assert len(fake.calls) == 3


async def test_rate_limit_is_retried_then_succeeds() -> None:
    fake = FakeGenerateContent([api_error(429, "RESOURCE_EXHAUSTED"), TEXT_RESPONSE])

    turn = await generate(provider_with(fake))

    assert turn.text == "Hello!"
    assert len(fake.calls) == 2


async def test_rate_limit_after_all_attempts_is_rate_limited_error() -> None:
    fake = FakeGenerateContent([api_error(429, "RESOURCE_EXHAUSTED")] * 3)

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
    fake = FakeGenerateContent([error, TEXT_RESPONSE])

    with pytest.raises(expected):
        await generate(provider_with(fake))

    assert len(fake.calls) == 1


async def test_slow_response_times_out_without_retry() -> None:
    fake = FakeGenerateContent([Stall(seconds=5), TEXT_RESPONSE])

    with pytest.raises(LLMTimeoutError) as exc_info:
        await generate(provider_with(fake, timeout_seconds=0.05))

    assert (exc_info.value.status_code, exc_info.value.code) == (504, "LLM_TIMEOUT")
    assert len(fake.calls) == 1


async def test_sdk_http_timeout_is_timeout_error() -> None:
    # google-genai 3.x는 httpx2로 요청한다. SDK의 HttpOptions timeout이 먼저 끝나면 이 예외가 올라온다.
    fake = FakeGenerateContent([httpx2.ReadTimeout("read timed out"), TEXT_RESPONSE])

    with pytest.raises(LLMTimeoutError):
        await generate(provider_with(fake))

    assert len(fake.calls) == 1


async def test_connection_error_is_retried_then_unavailable() -> None:
    fake = FakeGenerateContent([httpx2.ConnectError("refused")] * 2)

    with pytest.raises(LLMUnavailableError):
        await generate(provider_with(fake, max_attempts=2))

    assert len(fake.calls) == 2


async def test_blocked_prompt_returns_empty_turn() -> None:
    fake = FakeGenerateContent(
        [
            types.GenerateContentResponse(
                prompt_feedback=types.GenerateContentResponsePromptFeedback(block_reason=types.BlockedReason.SAFETY)
            )
        ]
    )

    turn = await generate(provider_with(fake))

    assert turn == ModelTurn(finish_reason="PROMPT_SAFETY")


async def test_aclose_closes_the_client() -> None:
    closed: list[bool] = []

    async def close() -> None:
        closed.append(True)

    provider = GeminiProvider(
        FakeGenerateContent([]),
        generate_content_stream=FakeGenerateContentStream([]),
        model="m",
        max_output_tokens=1,
        timeout_seconds=1,
        close=close,
    )
    await provider.aclose()

    assert closed == [True]

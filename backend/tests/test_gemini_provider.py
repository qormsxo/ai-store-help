"""GeminiProvider 단위 테스트. 네트워크를 쓰지 않는다.

조각 순서와 재시도는 test_gemini_streaming.py와 채팅 API 테스트가 본다.
여기는 대화가 Gemini 함수 호출 형식으로 가는지, 클라이언트 오류는 재시도하지 않는지만 본다.
"""

import pytest
from google.genai import types

from app.agent.tools import ToolDispatcher
from app.errors import LLMNotConfiguredError
from app.llm.gemini import GeminiProvider
from app.llm.types import ModelTurn, ToolCall, ToolResult, ToolResultTurn, UserTurn
from tests.fake_gemini import TEXT_RESPONSE, api_error, as_single_chunk_streams, provider_with


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


async def test_client_errors_are_not_retried() -> None:
    error = api_error(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key.")
    fake = as_single_chunk_streams([error, TEXT_RESPONSE])

    with pytest.raises(LLMNotConfiguredError):
        await generate(provider_with(fake))

    assert len(fake.calls) == 1

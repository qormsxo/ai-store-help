"""GeminiProvider.generate_stream 단위 테스트. 네트워크를 쓰지 않는다.

SDK의 generate_content_stream 자리에 가짜를 넣고, 진짜 google-genai 타입(청크, APIError)으로
조각 전달, Function Call 처리, 재시도 규칙, 타임아웃, 연결 닫기를 확인한다.
"""

import pytest
from google.genai import types

from app.agent.tools import ToolDispatcher
from app.errors import LLMUnavailableError
from app.llm.gemini import GeminiProvider
from app.llm.types import ModelTurn, StreamItem, TextDelta, ToolCall, UserTurn
from tests.fake_gemini import FakeGenerateContentStream, api_error, provider_with, response_with, text_response

def streaming(stream: FakeGenerateContentStream, *, timeout_seconds: float = 5) -> GeminiProvider:
    return provider_with(stream, timeout_seconds=timeout_seconds)

async def collect(provider: GeminiProvider) -> list[StreamItem]:
    return [
        item
        async for item in provider.generate_stream(
            system_instruction="system", turns=[UserTurn(text="Hi")], tools=ToolDispatcher().definitions()
        )
    ]

async def test_function_call_chunk_is_not_text_and_keeps_the_signature() -> None:
    stream = FakeGenerateContentStream(
        [
            [
                response_with(
                    types.Part(text="Let me think about this.", thought=True),
                    types.Part(
                        function_call=types.FunctionCall(id="call-1", name="search_knowledge", args={"query": "dogs"}),
                        thought_signature=b"signature-1",
                    ),
                )
            ]
        ]
    )

    items = await collect(streaming(stream))

    # 생각(thought) 파트와 Function Call은 TextDelta로 나오지 않는다. 호출은 마지막 ModelTurn에만 담긴다.
    assert items == [
        ModelTurn(
            tool_calls=[
                ToolCall(id="call-1", name="search_knowledge", arguments={"query": "dogs"}, signature=b"signature-1")
            ],
            finish_reason="STOP",
        )
    ]

async def test_stream_request_uses_manual_function_calling_and_the_full_context() -> None:
    stream = FakeGenerateContentStream([[text_response("OK")]])

    await collect(streaming(stream))

    (call,) = stream.calls
    assert call.model == "gemini-test-model"
    assert call.contents == [types.Content(role="user", parts=[types.Part(text="Hi")])]
    assert call.config.system_instruction == "system"
    assert call.config.max_output_tokens == 1024
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
    assert declarations[1].parameters_json_schema == ToolDispatcher().definitions()[1].parameters

# --- 재시도: 고객에게 아무것도 보내기 전에만 ----------------------------------------------------------

async def test_failure_before_any_text_is_retried() -> None:
    stream = FakeGenerateContentStream(
        [
            api_error(503, "UNAVAILABLE"),
            [api_error(500, "INTERNAL")],
            [text_response("Recovered.")],
        ]
    )

    items = await collect(streaming(stream))

    assert items == [TextDelta(text="Recovered."), ModelTurn(text="Recovered.", finish_reason="STOP")]
    assert len(stream.calls) == 3

async def test_failure_after_text_was_delivered_is_not_retried() -> None:
    """이미 화면에 나간 글자를 다시 받으면 중복된다. 재시도하지 않고 오류를 올린다."""
    stream = FakeGenerateContentStream([[text_response("Our store"), api_error(503, "UNAVAILABLE")], [text_response("x")]])
    items = streaming(stream).generate_stream(system_instruction="system", turns=[UserTurn(text="Hi")], tools=[])

    assert await anext(items) == TextDelta(text="Our store")
    with pytest.raises(LLMUnavailableError):
        await anext(items)
    assert len(stream.calls) == 1

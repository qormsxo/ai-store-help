"""GeminiProvider.generate_stream 단위 테스트. 네트워크를 쓰지 않는다.

SDK의 generate_content_stream 자리에 가짜를 넣고, 진짜 google-genai 타입(청크, APIError)으로
조각 전달, Function Call 처리, 재시도 규칙, 타임아웃, 연결 닫기를 확인한다.
"""

import pytest
from google.genai import types

from app.agent.tools import ToolDispatcher
from app.errors import LLMTimeoutError, LLMUnavailableError
from app.llm.gemini import GeminiProvider
from app.llm.types import ModelTurn, StreamItem, TextDelta, ToolCall, UserTurn
from tests.fake_gemini import (
    FakeGenerateContent,
    FakeGenerateContentStream,
    Stall,
    api_error,
    function_call_response,
    provider_with,
    text_response,
)

def streaming(stream: FakeGenerateContentStream, *, timeout_seconds: float = 5) -> GeminiProvider:
    return provider_with(FakeGenerateContent([]), stream, timeout_seconds=timeout_seconds)

async def collect(provider: GeminiProvider) -> list[StreamItem]:
    return [
        item
        async for item in provider.generate_stream(
            system_instruction="system", turns=[UserTurn(text="Hi")], tools=ToolDispatcher().definitions()
        )
    ]

async def test_text_chunks_are_delivered_in_order_then_the_assembled_turn() -> None:
    stream = FakeGenerateContentStream([[text_response("Our store"), text_response(" is open"), text_response(" 9 to 6.")]])

    items = await collect(streaming(stream))

    assert items == [
        TextDelta(text="Our store"),
        TextDelta(text=" is open"),
        TextDelta(text=" 9 to 6."),
        ModelTurn(text="Our store is open 9 to 6.", finish_reason="STOP"),
    ]

async def test_each_chunk_is_delivered_before_the_next_one_is_read() -> None:
    """응답을 다 받고 나서 내보내지 않는다. 첫 조각은 두 번째 청크가 오류를 내기 전에 이미 나온다."""
    stream = FakeGenerateContentStream([[text_response("First"), api_error(500, "INTERNAL")]])
    items = streaming(stream, timeout_seconds=5).generate_stream(
        system_instruction="system", turns=[UserTurn(text="Hi")], tools=[]
    )

    assert await anext(items) == TextDelta(text="First")
    with pytest.raises(LLMUnavailableError):
        await anext(items)

async def test_function_call_chunk_is_not_text_and_keeps_the_signature() -> None:
    stream = FakeGenerateContentStream([[function_call_response("search_knowledge", "call-1", query="dogs")]])

    items = await collect(streaming(stream))

    # Function Call은 TextDelta로 나오지 않고 마지막 ModelTurn에만 담긴다.
    assert items == [
        ModelTurn(
            tool_calls=[
                ToolCall(id="call-1", name="search_knowledge", arguments={"query": "dogs"}, signature=b"signature-call-1")
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
    assert call.config.automatic_function_calling == types.AutomaticFunctionCallingConfig(disable=True)
    declared = [d.name for tool in call.config.tools or [] for d in tool.function_declarations or []]
    assert declared == [
        "get_store_profile",
        "search_knowledge",
        "create_support_ticket",
        "reject_not_inquiry",
    ]

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

# --- 타임아웃, 연결 닫기 ----------------------------------------------------------------------------------

async def test_stalled_stream_times_out_after_the_chunks_received_so_far() -> None:
    stream = FakeGenerateContentStream([[text_response("Our store"), Stall(seconds=5)]])
    items = streaming(stream, timeout_seconds=0.05).generate_stream(
        system_instruction="system", turns=[UserTurn(text="Hi")], tools=[]
    )

    assert await anext(items) == TextDelta(text="Our store")
    with pytest.raises(LLMTimeoutError):
        await anext(items)
    assert stream.closed == 1

async def test_timeout_is_per_chunk_so_a_long_answer_is_not_cut_off() -> None:
    """청크가 계속 오면 전체 시간이 timeout보다 길어도 끊지 않는다."""
    chunks = [text_response(f"part{i} ") for i in range(4)]
    steps: list[types.GenerateContentResponse | Exception | Stall] = []
    for chunk in chunks:
        steps += [Stall(seconds=0.03), chunk]
    stream = FakeGenerateContentStream([steps])

    items = await collect(streaming(stream, timeout_seconds=0.06))

    assert items == [
        *[TextDelta(text=f"part{i} ") for i in range(4)],
        ModelTurn(text="part0 part1 part2 part3 ", finish_reason="STOP"),
    ]

async def test_closing_the_generator_closes_the_gemini_stream() -> None:
    """고객 연결이 끊겨 소비가 멈추면 Gemini 응답 연결도 같이 닫힌다."""
    stream = FakeGenerateContentStream([[text_response("A"), text_response("B"), text_response("C")]])
    items = streaming(stream).generate_stream(system_instruction="system", turns=[UserTurn(text="Hi")], tools=[])

    assert await anext(items) == TextDelta(text="A")
    await items.aclose()

    assert stream.closed == 1

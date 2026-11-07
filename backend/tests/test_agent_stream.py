"""AgentService.reply_stream + ToolDispatcher + 실제 PostgreSQL 테스트. LLM은 MockGeminiProvider(가짜)로 대체한다.

스트리밍과 비스트리밍이 같은 반복문(Tool Calling Loop)을 쓰는지, 스트리밍 특유의 규칙
(Tool 라운드는 텍스트로 나오지 않음, 앞뒤 텍스트 구분, 중간에 닫기)을 확인한다.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.service import AgentReply, AgentService, ToolExecution
from app.errors import AgentEmptyResponseError
from app.llm.types import ModelTurn, TextDelta
from app.models import Conversation, Store
from tests.factories import create_conversation, create_message, create_store
from tests.mock_llm import Chunks, MockGeminiProvider, final_text, function_call, observation, turn_kinds

@pytest.fixture
async def store(session: AsyncSession) -> Store:
    store = await create_store(session, slug="oak-pine-test", name="Oak & Pine Coffee Roasters")
    store.address = "1427 Oak Street, Portland, OR 97205"
    await session.flush()
    return store

@pytest.fixture
async def conversation(session: AsyncSession, store: Store) -> Conversation:
    return await create_conversation(session, store)

async def stream(
    provider: MockGeminiProvider, session: AsyncSession, conversation: Conversation, text: str = "Hello"
) -> list[TextDelta | AgentReply]:
    user_message = await create_message(session, conversation, text)
    return [event async for event in AgentService(provider).reply_stream(session, conversation, user_message, [])]

async def test_text_is_streamed_as_deltas_and_ends_with_the_reply(
    session: AsyncSession, conversation: Conversation
) -> None:
    provider = MockGeminiProvider([Chunks(["We open ", "at 7 AM", " daily."])])

    events = await stream(provider, session, conversation)

    assert events == [
        TextDelta(text="We open "),
        TextDelta(text="at 7 AM"),
        TextDelta(text=" daily."),
        AgentReply(content="We open at 7 AM daily.", tool_executions=[]),
    ]

async def test_tool_rounds_are_not_streamed_but_the_final_answer_is(
    session: AsyncSession, store: Store, conversation: Conversation
) -> None:
    provider = MockGeminiProvider([function_call("get_store_profile"), Chunks(["We are at ", "1427 Oak Street."])])

    events = await stream(provider, session, conversation, "Where are you?")

    # Function Call과 그 결과는 텍스트로 나오지 않는다. 텍스트는 최종 답변 조각뿐이다.
    assert events == [
        TextDelta(text="We are at "),
        TextDelta(text="1427 Oak Street."),
        AgentReply(
            content="We are at 1427 Oak Street.",
            tool_executions=[ToolExecution(name="get_store_profile", is_error=False)],
        ),
    ]
    # 같은 반복문이 Tool을 실행하고 결과를 다음 호출에 붙였다.
    assert [turn_kinds(request.turns) for request in provider.requests] == [
        ["user"],
        ["user", "model:get_store_profile", "observation"],
    ]
    assert observation(provider.requests[1]).results[0].name == "get_store_profile"

async def test_text_before_a_tool_call_is_kept_apart_from_the_final_answer(
    session: AsyncSession, conversation: Conversation
) -> None:
    """모델이 Tool을 부르기 전에 텍스트를 먼저 냈다면, 그 텍스트도 이미 화면에 나갔다.

    저장하는 답변이 화면에 보인 것과 같도록 두 텍스트를 줄바꿈으로 구분해 모두 포함한다.
    """
    provider = MockGeminiProvider(
        [
            Chunks(["Let me check."], tool_calls=function_call("get_store_profile").tool_calls),
            Chunks(["We open ", "at 7 AM."]),
        ]
    )

    events = await stream(provider, session, conversation)

    assert events == [
        TextDelta(text="Let me check."),
        TextDelta(text="\n\n"),
        TextDelta(text="We open "),
        TextDelta(text="at 7 AM."),
        AgentReply(
            content="Let me check.\n\nWe open at 7 AM.",
            tool_executions=[ToolExecution(name="get_store_profile", is_error=False)],
        ),
    ]

async def test_empty_final_answer_is_an_error(session: AsyncSession, conversation: Conversation) -> None:
    provider = MockGeminiProvider([ModelTurn(text="  ", finish_reason="MAX_TOKENS")])

    with pytest.raises(AgentEmptyResponseError):
        await stream(provider, session, conversation)

async def test_closing_the_stream_stops_the_llm_call_and_runs_no_more_rounds(
    session: AsyncSession, conversation: Conversation
) -> None:
    user_message = await create_message(session, conversation, "Hello")
    provider = MockGeminiProvider([Chunks(["First", "never reached"]), final_text("unused")])
    events = AgentService(provider).reply_stream(session, conversation, user_message, [])

    assert await anext(events) == TextDelta(text="First")
    await events.aclose()

    # 열려 있던 LLM 스트림이 닫혔고, 이후 라운드는 시작하지 않았다.
    assert provider.interrupted_streams == 1
    assert len(provider.requests) == 1

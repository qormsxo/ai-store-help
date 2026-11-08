"""스트리밍에서만 있는 규칙. 글자 조각과 도구 숨김, 빈 답변, 연결 종료는 API 스트림 테스트가 본다."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.service import AgentReply, AgentService, ToolExecution
from app.llm.types import TextDelta
from app.models import Conversation, Store
from tests.factories import create_conversation, create_message, create_store
from tests.mock_llm import Chunks, MockGeminiProvider, function_call

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


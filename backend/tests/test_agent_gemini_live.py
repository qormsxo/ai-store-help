"""실제 Gemini API를 호출하는 통합 테스트. 기본 실행에서는 빠진다.

실행: GEMINI_API_KEY, GEMINI_MODEL을 설정하고 `pytest -m live`
LLM 응답은 매번 조금씩 다르므로 문장 대신 실행된 Tool과 DB 결과만 확인한다.
"""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.service import AgentReply, AgentService
from app.config import get_settings
from app.llm.factory import create_llm_provider
from app.llm.provider import LLMProvider
from app.models import Conversation, Store, SupportTicket
from tests.factories import create_business_hour, create_knowledge, create_message, create_store

_settings = get_settings()

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        _settings.llm_provider != "gemini"
        or not (_settings.gemini_api_key and _settings.gemini_api_key.get_secret_value() and _settings.gemini_model),
        reason="LLM_PROVIDER=gemini, GEMINI_API_KEY, GEMINI_MODEL이 있어야 실제 Gemini 테스트를 실행한다.",
    ),
]


@pytest.fixture
async def provider() -> AsyncIterator[LLMProvider]:
    provider = create_llm_provider(get_settings())
    yield provider
    await provider.aclose()


@pytest.fixture
async def store(session: AsyncSession) -> Store:
    store = await create_store(session, slug="oak-pine-live", name="오크앤파인 커피")
    store.address = "서울특별시 마포구 연남로 12"
    store.phone = "02-555-0142"
    for day in range(6):
        await create_business_hour(session, store, day)
    await create_business_hour(session, store, 6, None, None)
    await session.flush()
    return store


async def ask(session: AsyncSession, conversation: Conversation, provider: LLMProvider, text: str) -> AgentReply:
    user_message = await create_message(session, conversation, text)
    reply: AgentReply | None = None
    async for event in AgentService(provider).reply_stream(session, conversation, user_message, []):
        match event:
            case AgentReply() as done:
                reply = done
    if reply is None:
        raise AssertionError("답변이 없습니다.")
    return reply


async def test_live_scenario_a_store_address(
    session: AsyncSession, store: Store, conversation: Conversation, provider: LLMProvider
) -> None:
    reply = await ask(session, conversation, provider, "매장 주소가 어디인가요?")

    assert [e.name for e in reply.tool_executions] == ["get_store_profile"]
    assert "연남로" in reply.content


async def test_live_scenario_b_knowledge(
    session: AsyncSession, store: Store, conversation: Conversation, provider: LLMProvider
) -> None:
    await create_knowledge(
        session, store, "매장 안에 강아지 출입이 되나요?", "안내견만 매장 안으로 들어올 수 있습니다."
    )

    reply = await ask(session, conversation, provider, "강아지 데려가도 되나요?")

    assert [e.name for e in reply.tool_executions][:2] == ["get_store_profile", "search_knowledge"]
    assert "create_support_ticket" not in [e.name for e in reply.tool_executions]


async def test_live_scenario_c_ticket(
    session: AsyncSession, store: Store, conversation: Conversation, provider: LLMProvider
) -> None:
    reply = await ask(session, conversation, provider, "60일이 지난 뒤에도 환불받을 수 있나요?")

    assert [e.name for e in reply.tool_executions] == [
        "get_store_profile",
        "search_knowledge",
        "create_support_ticket",
    ]
    ticket = await session.scalar(select(SupportTicket).where(SupportTicket.conversation_id == conversation.id))
    assert ticket is not None

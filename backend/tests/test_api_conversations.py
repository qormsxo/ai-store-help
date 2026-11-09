"""고객 채팅 API 테스트.

실제 FastAPI 라우터, reply_service, AgentService, ToolDispatcher, PostgreSQL을 쓰고 LLM만 가짜로 바꾼다.
답변은 SSE 스트림으로만 받는다. 스트림이 시작되기 전의 실패(404, 409, 422, 저장 실패)는 JSON 오류다.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.service import AgentService
from app.api.deps import get_agent_service
from app.config import get_settings
from app.db import get_session
from app.errors import AgentLimitExceededError, LLMNotConfiguredError, LLMTimeoutError
from app.llm.provider import LLMProvider
from app.llm.types import ModelTurn, UserTurn
from app.main import app
from app.models import Message, MessageRole, Store, SupportTicket
from app.services.conversation_service import HISTORY_LIMIT
from tests.factories import create_conversation, create_knowledge, create_message, create_store
from tests.fake_gemini import api_error, as_single_chunk_streams, provider_with
from tests.mock_llm import MockGeminiProvider, final_text, function_call, turn_kinds

REFUND_QUESTION = "60일이 지난 뒤에도 환불받을 수 있나요?"
TICKET_CALL = {"question": REFUND_QUESTION, "escalation_reason": "no_knowledge"}


@pytest.fixture
async def store(session: AsyncSession) -> Store:
    """API가 응대하는 기본 매장. API 요청이 테스트와 같은 DB 세션을 쓰게 한다."""
    app.dependency_overrides[get_session] = lambda: session
    store = await create_store(session, slug=get_settings().default_store_slug, name="Oak & Pine Coffee Roasters")
    store.address = "1427 Oak Street, Portland, OR 97205"
    await session.flush()
    return store


def use_agent(provider: LLMProvider) -> None:
    app.dependency_overrides[get_agent_service] = lambda: AgentService(provider)


async def start_conversation(client: httpx.AsyncClient, visitor_id: str = "visitor-1") -> str:
    res = await client.post("/api/conversations", json={"visitor_id": visitor_id})
    assert res.status_code == 201
    return res.json()["id"]


async def send(client: httpx.AsyncClient, conversation_id: str, content: str) -> httpx.Response:
    return await client.post(f"/api/conversations/{conversation_id}/messages/stream", json={"content": content})


def assert_completed(res: httpx.Response) -> None:
    assert res.status_code == 200
    assert "event: completed\n" in res.text
    assert "event: error\n" not in res.text


def stream_error(res: httpx.Response) -> dict[str, str]:
    """스트림이 시작된 뒤의 실패. HTTP는 200이고 마지막 이벤트는 error다."""
    assert res.status_code == 200
    blocks = [block for block in res.text.split("\n\n") if block.startswith("event: error\n")]
    assert len(blocks) == 1
    data = json.loads(blocks[0].split("data: ", 1)[1])
    assert set(data) == {"code", "message"}
    return data


async def stored_messages(session: AsyncSession, conversation_id: str) -> list[Message]:
    result = await session.scalars(
        select(Message).where(Message.conversation_id == uuid.UUID(conversation_id)).order_by(Message.created_at)
    )
    return list(result.all())


async def ticket_count(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(SupportTicket)) or 0


def error_of(res: httpx.Response) -> dict[str, str]:
    """오류 응답은 {"error": {"code", "message"}}뿐이어야 한다(내부 정보 없음)."""
    body = res.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    return body["error"]


# --- 1~3. 대화 생성, 조회, 메시지 목록 ---------------------------------------------------


async def test_list_messages_in_time_order(client: httpx.AsyncClient, session: AsyncSession, store: Store) -> None:
    conversation = await create_conversation(session, store)
    await create_message(session, conversation, "first question", MessageRole.USER)
    await create_message(session, conversation, "first answer", MessageRole.ASSISTANT)
    await create_message(session, conversation, "internal note", MessageRole.SYSTEM)
    await create_message(session, conversation, "second question", MessageRole.USER)

    res = await client.get(f"/api/conversations/{conversation.id}/messages")

    assert res.status_code == 200
    # system 메시지는 고객 대화가 아니므로 나오지 않는다.
    assert [(m["role"], m["content"]) for m in res.json()] == [
        ("user", "first question"),
        ("assistant", "first answer"),
        ("user", "second question"),
    ]
    assert set(res.json()[0]) == {"id", "role", "content", "created_at"}


# --- 11. 없는 대화, 다른 매장 ------------------------------------------------------------------


async def test_other_store_conversation_is_hidden(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    other_store = await create_store(session, slug="other-store")
    other = await create_conversation(session, other_store)
    provider = MockGeminiProvider([final_text("never")])
    use_agent(provider)

    # 존재 여부를 알려주지 않도록 없는 대화와 같은 404다.
    for res in (
        await client.get(f"/api/conversations/{other.id}"),
        await client.get(f"/api/conversations/{other.id}/messages"),
        await send(client, str(other.id), "Hi"),
    ):
        assert res.status_code == 404
        assert error_of(res)["code"] == "CONVERSATION_NOT_FOUND"
    assert provider.requests == []
    assert await stored_messages(session, str(other.id)) == []


async def test_expired_exact_question_is_not_reused(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    await create_knowledge(
        session,
        store,
        question="Are dogs allowed in the store?",
        answer="Yes, dogs are welcome.",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    conversation_id = await start_conversation(client, "visitor-2")
    provider = MockGeminiProvider([final_text("I need to check that.")])
    use_agent(provider)

    res = await send(client, conversation_id, "Are dogs allowed in the store?")

    assert_completed(res)
    assert provider.requests != []
    assert [m.content for m in await stored_messages(session, conversation_id)] == [
        "Are dogs allowed in the store?",
        "I need to check that.",
    ]


async def test_same_question_as_stored_knowledge_is_answered_without_a_new_ticket(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    await create_knowledge(
        session, store, question="Are dogs allowed in the store?", answer="Yes, dogs are welcome."
    )
    conversation_id = await start_conversation(client, "visitor-2")
    provider = MockGeminiProvider(
        [
            function_call(
                "create_support_ticket",
                question="Are dogs allowed in the store?",
                escalation_reason="no_knowledge",
            ),
            final_text("I've forwarded your question."),
        ]
    )
    use_agent(provider)

    res = await send(client, conversation_id, "are   DOGS allowed in the store?")

    assert_completed(res)
    assert provider.requests == []
    assert await ticket_count(session) == 0
    assert [m.content for m in await stored_messages(session, conversation_id)] == [
        "are   DOGS allowed in the store?",
        "Yes, dogs are welcome.",
    ]


# --- 7, 8. 대화 문맥 -----------------------------------------------------------------------------


async def test_previous_exchange_is_sent_as_context(client: httpx.AsyncClient, store: Store) -> None:
    conversation_id = await start_conversation(client)
    provider = MockGeminiProvider([final_text("Yes, we have oat milk."), final_text("It is $0.50 extra.")])
    use_agent(provider)

    await send(client, conversation_id, "Do you have oat milk?")
    await send(client, conversation_id, "Is it extra?")

    assert provider.requests[0].turns == (UserTurn(text="Do you have oat milk?"),)
    assert provider.requests[1].turns == (
        UserTurn(text="Do you have oat milk?"),
        ModelTurn(text="Yes, we have oat milk."),
        UserTurn(text="Is it extra?"),
    )


async def test_context_is_limited_to_recent_messages(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    conversation = await create_conversation(session, store)
    for number in range(HISTORY_LIMIT + 5):
        await create_message(session, conversation, f"question {number}", MessageRole.USER)
    provider = MockGeminiProvider([final_text("OK")])
    use_agent(provider)

    await send(client, str(conversation.id), "latest")

    turns = provider.requests[0].turns
    assert len(turns) == HISTORY_LIMIT + 1
    assert turns[0] == UserTurn(text="question 5")
    assert turns[-1] == UserTurn(text="latest")


async def test_conversations_do_not_share_context(client: httpx.AsyncClient, store: Store) -> None:
    first = await start_conversation(client, "visitor-a")
    second = await start_conversation(client, "visitor-b")
    provider = MockGeminiProvider([final_text("A1"), final_text("B1"), final_text("A2"), final_text("B2")])
    use_agent(provider)

    await send(client, first, "A: my order number is 1234")
    await send(client, second, "B: hello")
    await send(client, first, "A: what was my number?")
    await send(client, second, "B: bye")

    assert provider.requests[2].turns == (
        UserTurn(text="A: my order number is 1234"),
        ModelTurn(text="A1"),
        UserTurn(text="A: what was my number?"),
    )
    assert provider.requests[3].turns == (
        UserTurn(text="B: hello"),
        ModelTurn(text="B1"),
        UserTurn(text="B: bye"),
    )


# --- 9, 10. 한 요청에서 다중 Tool Calling ------------------------------------------------------------


async def test_multi_tool_request_creates_ticket_for_this_message(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    conversation_id = await start_conversation(client)
    provider = MockGeminiProvider(
        [
            function_call("get_store_profile"),
            function_call("search_knowledge", query="환불 60일"),
            function_call("create_support_ticket", **TICKET_CALL),
            final_text("I've forwarded your question to our staff."),
        ]
    )
    use_agent(provider)

    res = await send(client, conversation_id, REFUND_QUESTION)

    assert_completed(res)
    saved = await stored_messages(session, conversation_id)
    assert saved[-1].content == "I've forwarded your question to our staff."
    assert turn_kinds(provider.requests[3].turns) == [
        "user",
        "model:get_store_profile",
        "observation",
        "model:search_knowledge",
        "observation",
        "model:create_support_ticket",
        "observation",
    ]
    ticket = await session.scalar(select(SupportTicket))
    assert ticket is not None
    assert (str(ticket.conversation_id), str(ticket.source_message_id), ticket.store_id) == (
        conversation_id,
        str(saved[0].id),
        store.id,
    )
    # 대화 메시지에는 일반 대화(고객 질문, 최종 답변)만 저장된다. Tool 호출과 Observation은 저장하지 않는다.
    assert [m.role for m in await stored_messages(session, conversation_id)] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]


# --- 12. Gemini API 오류 ------------------------------------------------------------------------------


async def test_gemini_errors_become_safe_error_responses_and_keep_user_message(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    conversation_id = await start_conversation(client)
    use_agent(
        provider_with(
            as_single_chunk_streams([api_error(429, "RESOURCE_EXHAUSTED", "Quota exceeded for project 123")] * 3)
        )
    )

    res = await send(client, conversation_id, "Do you allow dogs inside?")

    assert stream_error(res)["code"] == "LLM_RATE_LIMITED"
    # 구글이 준 오류 문구, 키, IP 같은 내부 정보는 고객 응답에 없다.
    for leaked in ("AIzaSy", "10.0.0.1", "project 123", "RESOURCE_EXHAUSTED", "bad request details"):
        assert leaked not in res.text
    assert [(m.role, m.content) for m in await stored_messages(session, conversation_id)] == [
        (MessageRole.USER, "Do you allow dogs inside?")
    ]


async def test_missing_gemini_configuration_is_503(client: httpx.AsyncClient, session: AsyncSession, store: Store) -> None:
    conversation_id = await start_conversation(client)

    def not_configured() -> AgentService:
        raise LLMNotConfiguredError("GEMINI_API_KEY가 설정되지 않았습니다.")

    app.dependency_overrides[get_agent_service] = not_configured

    res = await send(client, conversation_id, "Hi")

    assert res.status_code == 503
    assert error_of(res)["code"] == "LLM_NOT_CONFIGURED"
    # AI를 쓸 수 없는 상태에서는 메시지를 받지 않는다. 답 못 받을 메시지가 대화에 쌓이지 않게 한다.
    assert await stored_messages(session, conversation_id) == []


# --- 13. Agent 실행 실패 시 고객 메시지 보존 ------------------------------------------------------------


async def test_agent_failure_keeps_user_message_and_rolls_back_ticket(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    conversation_id = await start_conversation(client)
    # 티켓까지 만든 뒤 마지막 LLM 호출이 실패하는 경우. 답변 없이 티켓만 남으면 안 된다.
    failure = AgentLimitExceededError("too many calls")
    use_agent(MockGeminiProvider([function_call("create_support_ticket", **TICKET_CALL), failure]))

    res = await send(client, conversation_id, REFUND_QUESTION)

    assert stream_error(res)["code"] == "AGENT_LIMIT_EXCEEDED"
    assert [(m.role, m.content) for m in await stored_messages(session, conversation_id)] == [
        (MessageRole.USER, REFUND_QUESTION)
    ]
    assert await ticket_count(session) == 0
    listed = await client.get(f"/api/conversations/{conversation_id}/messages")
    assert [(m["role"], m["content"]) for m in listed.json()] == [("user", REFUND_QUESTION)]


# --- 14. 중복 메시지 저장 방지 ---------------------------------------------------------------------------


async def test_retry_after_failure_reuses_saved_message_and_creates_one_ticket(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    conversation_id = await start_conversation(client)
    provider = MockGeminiProvider(
        [
            function_call("create_support_ticket", **TICKET_CALL),
            LLMTimeoutError("timeout"),
            function_call("create_support_ticket", **TICKET_CALL),
            final_text("Forwarded."),
        ]
    )
    use_agent(provider)

    failed = await send(client, conversation_id, REFUND_QUESTION)
    kept = await stored_messages(session, conversation_id)
    retried = await send(client, conversation_id, REFUND_QUESTION)

    assert stream_error(failed)["code"] == "LLM_TIMEOUT"
    assert_completed(retried)
    # 재시도는 처음 저장된 고객 메시지를 그대로 쓴다. 메시지가 두 번 저장되지 않는다.
    assert (await stored_messages(session, conversation_id))[0].id == kept[0].id
    saved = await stored_messages(session, conversation_id)
    assert [(m.role, m.content) for m in saved] == [
        (MessageRole.USER, REFUND_QUESTION),
        (MessageRole.ASSISTANT, "Forwarded."),
    ]
    ticket = await session.scalar(select(SupportTicket))
    assert ticket is not None
    assert ticket.source_message_id == kept[0].id
    assert await ticket_count(session) == 1
    # 재시도 때 Agent가 받은 Context에는 같은 질문이 한 번만 들어 있다.
    assert provider.requests[2].turns == (UserTurn(text=REFUND_QUESTION),)


async def test_same_text_after_an_answer_is_a_new_message(client: httpx.AsyncClient, session: AsyncSession, store: Store) -> None:
    conversation_id = await start_conversation(client)
    use_agent(MockGeminiProvider([final_text("Yes."), final_text("Still yes.")]))

    await send(client, conversation_id, "Do you have oat milk?")
    await send(client, conversation_id, "Do you have oat milk?")

    saved = await stored_messages(session, conversation_id)
    assert saved[0].id != saved[2].id
    assert [m.role for m in await stored_messages(session, conversation_id)] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]

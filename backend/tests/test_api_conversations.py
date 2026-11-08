"""고객 채팅 API 테스트.

실제 FastAPI 라우터, reply_service, AgentService, ToolDispatcher, PostgreSQL을 쓰고 LLM만 가짜로 바꾼다.
답변은 SSE 스트림으로만 받는다. 스트림이 시작되기 전의 실패(404, 409, 422, 저장 실패)는 JSON 오류다.
"""

import json
import uuid

import httpx
import pytest
from google.genai import types
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.service import AgentService
from app.api.deps import get_agent_service
from app.config import get_settings
from app.db import get_session
from app.errors import (
    AgentEmptyResponseError,
    AgentLimitExceededError,
    LLMNotConfiguredError,
    LLMTimeoutError,
)
from app.llm.provider import LLMProvider
from app.llm.types import ModelTurn, UserTurn
from app.main import app
from app.models import Conversation, Message, MessageRole, Store, SupportTicket
from app.schemas import MAX_MESSAGE_LENGTH
from app.services.conversation_service import HISTORY_LIMIT
from tests.factories import create_conversation, create_message, create_store
from tests.fake_gemini import (
    Stall,
    api_error,
    as_single_chunk_streams,
    function_call_response,
    provider_with,
    text_response,
)
from tests.mock_llm import MockGeminiProvider, final_text, function_call, turn_kinds

REFUND_QUESTION = "Can I get a refund after 60 days?"
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


async def test_create_conversation(client: httpx.AsyncClient, session: AsyncSession, store: Store) -> None:
    res = await client.post("/api/conversations", json={"visitor_id": "visitor-1"})

    assert res.status_code == 201
    body = res.json()
    assert set(body) == {"id", "visitor_id", "created_at"}
    assert body["visitor_id"] == "visitor-1"
    saved = await session.get(Conversation, uuid.UUID(body["id"]))
    assert saved is not None
    # 현재 매장과 연결되고, 응답에는 매장 id를 노출하지 않는다.
    assert saved.store_id == store.id


async def test_same_visitor_can_start_several_independent_conversations(
    client: httpx.AsyncClient, store: Store
) -> None:
    first = await start_conversation(client, "visitor-1")
    second = await start_conversation(client, "visitor-1")

    assert first != second


@pytest.mark.parametrize("body", [{}, {"visitor_id": ""}, {"visitor_id": "   "}, {"visitor_id": "v" * 65}])
async def test_create_conversation_rejects_invalid_visitor_id(
    client: httpx.AsyncClient, store: Store, body: dict[str, str]
) -> None:
    res = await client.post("/api/conversations", json=body)

    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_get_conversation(client: httpx.AsyncClient, store: Store) -> None:
    conversation_id = await start_conversation(client, "visitor-7")

    res = await client.get(f"/api/conversations/{conversation_id}")

    assert res.status_code == 200
    assert (res.json()["id"], res.json()["visitor_id"]) == (conversation_id, "visitor-7")


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


async def test_list_messages_of_new_conversation_is_empty(client: httpx.AsyncClient, store: Store) -> None:
    conversation_id = await start_conversation(client)

    res = await client.get(f"/api/conversations/{conversation_id}/messages")

    assert res.json() == []


# --- 11. 없는 대화, 다른 매장 ------------------------------------------------------------------


@pytest.mark.parametrize("path", ["", "/messages"])
async def test_unknown_conversation_is_404(client: httpx.AsyncClient, store: Store, path: str) -> None:
    res = await client.get(f"/api/conversations/{uuid.uuid4()}{path}")

    assert res.status_code == 404
    assert error_of(res) == {"code": "CONVERSATION_NOT_FOUND", "message": "대화를 찾을 수 없습니다."}


async def test_post_message_to_unknown_conversation_is_404_and_skips_llm(
    client: httpx.AsyncClient, store: Store
) -> None:
    provider = MockGeminiProvider([final_text("never")])
    use_agent(provider)

    res = await send(client, str(uuid.uuid4()), "Hi")

    assert res.status_code == 404
    assert error_of(res)["code"] == "CONVERSATION_NOT_FOUND"
    assert provider.requests == []


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


async def test_malformed_conversation_id_is_validation_error(client: httpx.AsyncClient, store: Store) -> None:
    res = await client.get("/api/conversations/not-a-uuid")

    assert res.status_code == 422


# --- 메시지 입력 검증 ----------------------------------------------------------------------


@pytest.mark.parametrize("content", ["", "   ", "\n\t "])
async def test_empty_message_is_rejected_before_llm(
    client: httpx.AsyncClient, session: AsyncSession, store: Store, content: str
) -> None:
    conversation_id = await start_conversation(client)
    provider = MockGeminiProvider([final_text("never")])
    use_agent(provider)

    res = await send(client, conversation_id, content)

    assert res.status_code == 422
    assert provider.requests == []
    assert await stored_messages(session, conversation_id) == []


async def test_too_long_message_is_rejected_before_llm(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    conversation_id = await start_conversation(client)
    provider = MockGeminiProvider([final_text("never")])
    use_agent(provider)

    res = await send(client, conversation_id, "a" * (MAX_MESSAGE_LENGTH + 1))

    assert res.status_code == 422
    assert provider.requests == []
    assert await stored_messages(session, conversation_id) == []


async def test_message_at_max_length_is_accepted(client: httpx.AsyncClient, store: Store) -> None:
    conversation_id = await start_conversation(client)
    use_agent(MockGeminiProvider([final_text("OK")]))

    res = await send(client, conversation_id, "a" * MAX_MESSAGE_LENGTH)

    assert_completed(res)


async def test_missing_content_field_is_rejected(client: httpx.AsyncClient, store: Store) -> None:
    conversation_id = await start_conversation(client)

    res = await client.post(f"/api/conversations/{conversation_id}/messages/stream", json={})

    assert res.status_code == 422


# --- 4, 6. 고객 메시지와 AI 응답 저장 ----------------------------------------------------------


async def test_send_message_saves_user_message_and_assistant_reply(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    conversation_id = await start_conversation(client)
    use_agent(MockGeminiProvider([function_call("get_store_profile"), final_text("We are at 1427 Oak Street.")]))

    res = await send(client, conversation_id, "  What is your store address?  ")

    assert_completed(res)
    saved = await stored_messages(session, conversation_id)
    # 앞뒤 공백은 정리해서 저장한다.
    assert [(m.role, m.content) for m in saved] == [
        (MessageRole.USER, "What is your store address?"),
        (MessageRole.ASSISTANT, "We are at 1427 Oak Street."),
    ]
    listed = await client.get(f"/api/conversations/{conversation_id}/messages")
    assert [m["id"] for m in listed.json()] == [str(m.id) for m in saved]


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
            function_call("search_knowledge", query="refund after 60 days"),
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


async def test_real_gemini_provider_chain_links_function_calls_and_responses(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    """GeminiProvider(진짜) → AgentService → API. SDK 호출만 가짜라서 Gemini 형식 그대로 검증한다."""
    conversation_id = await start_conversation(client)
    fake = as_single_chunk_streams(
        [
            function_call_response("get_store_profile", "call-1"),
            function_call_response("search_knowledge", "call-2", query="refund 60 days"),
            function_call_response("create_support_ticket", "call-3", **TICKET_CALL),
            text_response("I've forwarded your question to our staff."),
        ]
    )
    use_agent(provider_with(fake))

    res = await send(client, conversation_id, REFUND_QUESTION)

    assert_completed(res)
    saved = await stored_messages(session, conversation_id)
    assert saved[-1].content == "I've forwarded your question to our staff."
    assert len(fake.calls) == 4
    assert [c.role for c in fake.calls[0].contents] == ["user"]
    assert fake.calls[0].contents[0].parts == [types.Part(text=REFUND_QUESTION)]

    expected = [("call-1", "get_store_profile"), ("call-2", "search_knowledge"), ("call-3", "create_support_ticket")]
    for index in (1, 2, 3):
        contents = fake.calls[index].contents
        # 요청마다 이전 Context 전체 + (모델의 Function Call, 그 결과)가 붙는다.
        assert [c.role for c in contents] == ["user"] + ["model", "user"] * index
        for position, (call_id, name) in enumerate(expected[:index]):
            model_content, response_content = contents[1 + 2 * position], contents[2 + 2 * position]
            (function_call_part,) = model_content.parts or []
            (function_response_part,) = response_content.parts or []
            assert function_call_part.function_call is not None
            assert (function_call_part.function_call.id, function_call_part.function_call.name) == (call_id, name)
            # Function Response는 짝이 되는 Function Call과 같은 id, name이다. thought signature도 그대로 돌아간다.
            assert function_response_part.function_response is not None
            assert (function_response_part.function_response.id, function_response_part.function_response.name) == (
                call_id,
                name,
            )
            assert function_call_part.thought_signature == f"signature-{call_id}".encode()

    # create_support_ticket의 Function Response에는 실제로 만든 티켓 id가 들어 있다.
    ticket = await session.scalar(select(SupportTicket))
    assert ticket is not None
    last_response = (fake.calls[3].contents[-1].parts or [])[0].function_response
    assert last_response is not None
    assert (last_response.response or {})["ticket_id"] == str(ticket.id)


# --- 12. Gemini API 오류 ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("outcomes", "timeout_seconds", "code"),
    [
        ([api_error(429, "RESOURCE_EXHAUSTED", "Quota exceeded for project 123")] * 3, 5, "LLM_RATE_LIMITED"),
        ([api_error(503, "UNAVAILABLE", "backend 10.0.0.1 overloaded")] * 3, 5, "LLM_UNAVAILABLE"),
        ([api_error(400, "INVALID_ARGUMENT", "API key not valid: AIzaSyFAKE")], 5, "LLM_NOT_CONFIGURED"),
        ([api_error(400, "INVALID_ARGUMENT", "bad request details")], 5, "LLM_REQUEST_FAILED"),
        ([Stall(seconds=5)], 0.05, "LLM_TIMEOUT"),
    ],
)
async def test_gemini_errors_become_safe_error_responses_and_keep_user_message(
    client: httpx.AsyncClient,
    session: AsyncSession,
    store: Store,
    outcomes: list[types.GenerateContentResponse | Exception | Stall],
    timeout_seconds: float,
    code: str,
) -> None:
    conversation_id = await start_conversation(client)
    use_agent(provider_with(as_single_chunk_streams(list(outcomes)), timeout_seconds=timeout_seconds))

    res = await send(client, conversation_id, "Do you allow dogs inside?")

    assert stream_error(res)["code"] == code
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


async def test_non_inquiry_returns_422_and_does_not_save_an_answer(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    conversation_id = await start_conversation(client)
    use_agent(MockGeminiProvider([function_call("reject_not_inquiry")]))

    res = await send(client, conversation_id, "How is the weather?")

    assert stream_error(res) == {"code": "NOT_STORE_INQUIRY", "message": "매장에 대한 문의만 답변할 수 있습니다."}
    assert [(m.role, m.content) for m in await stored_messages(session, conversation_id)] == [
        (MessageRole.USER, "How is the weather?")
    ]
    assert await ticket_count(session) == 0


# --- 13. Agent 실행 실패 시 고객 메시지 보존 ------------------------------------------------------------


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (LLMTimeoutError("timeout"), "LLM_TIMEOUT"),
        (AgentLimitExceededError("too many calls"), "AGENT_LIMIT_EXCEEDED"),
        (AgentEmptyResponseError("empty"), "AGENT_EMPTY_RESPONSE"),
    ],
)
async def test_agent_failure_keeps_user_message_and_rolls_back_ticket(
    client: httpx.AsyncClient,
    session: AsyncSession,
    store: Store,
    failure: Exception,
    code: str,
) -> None:
    conversation_id = await start_conversation(client)
    # 티켓까지 만든 뒤 마지막 LLM 호출이 실패하는 경우. 답변 없이 티켓만 남으면 안 된다.
    use_agent(MockGeminiProvider([function_call("create_support_ticket", **TICKET_CALL), failure]))

    res = await send(client, conversation_id, REFUND_QUESTION)

    assert stream_error(res)["code"] == code
    assert [(m.role, m.content) for m in await stored_messages(session, conversation_id)] == [
        (MessageRole.USER, REFUND_QUESTION)
    ]
    assert await ticket_count(session) == 0
    listed = await client.get(f"/api/conversations/{conversation_id}/messages")
    assert [(m["role"], m["content"]) for m in listed.json()] == [("user", REFUND_QUESTION)]


async def test_reply_save_failure_keeps_user_message_and_rolls_back_ticket(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    conversation_id = await start_conversation(client)
    # NUL 문자는 PostgreSQL text에 저장할 수 없다. 실제 DB 오류로 답변 저장 실패를 만든다.
    use_agent(MockGeminiProvider([function_call("create_support_ticket", **TICKET_CALL), final_text("bad\x00reply")]))

    res = await send(client, conversation_id, REFUND_QUESTION)

    assert stream_error(res) == {
        "code": "MESSAGE_SAVE_FAILED",
        "message": "메시지를 저장하지 못했습니다. 같은 메시지를 다시 보내 주세요.",
    }
    assert [m.role for m in await stored_messages(session, conversation_id)] == [MessageRole.USER]
    assert await ticket_count(session) == 0


async def test_user_message_save_failure_saves_nothing(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    conversation_id = await start_conversation(client)
    provider = MockGeminiProvider([final_text("never")])
    use_agent(provider)

    res = await send(client, conversation_id, "bad\x00message")

    assert res.status_code == 503
    assert error_of(res)["code"] == "MESSAGE_SAVE_FAILED"
    assert provider.requests == []
    assert await stored_messages(session, conversation_id) == []


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


async def test_different_text_after_failure_is_saved_as_a_new_message(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    conversation_id = await start_conversation(client)
    provider = MockGeminiProvider([LLMTimeoutError("timeout"), final_text("We open at 7.")])
    use_agent(provider)

    await send(client, conversation_id, "What time do you open?")
    res = await send(client, conversation_id, "Sorry, I mean on Sundays?")

    assert_completed(res)
    assert [(m.role, m.content) for m in await stored_messages(session, conversation_id)] == [
        (MessageRole.USER, "What time do you open?"),
        (MessageRole.USER, "Sorry, I mean on Sundays?"),
        (MessageRole.ASSISTANT, "We open at 7."),
    ]
    # 답을 받지 못한 이전 질문도 문맥에 남는다.
    assert provider.requests[1].turns == (
        UserTurn(text="What time do you open?"),
        UserTurn(text="Sorry, I mean on Sundays?"),
    )

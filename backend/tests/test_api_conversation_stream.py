"""고객 채팅 SSE 스트리밍 API 테스트.

실제 FastAPI 라우터, reply_service, AgentService, ToolDispatcher, PostgreSQL을 쓰고 LLM만 가짜로 바꾼다.
- MockGeminiProvider(Chunks): 텍스트 조각을 미리 정한 대로 내보내는 가짜 Provider
실제 Gemini API는 호출하지 않는다. Gemini 오류 매핑은 고객 채팅 API 테스트가 본다.

httpx의 ASGITransport는 응답을 끝까지 모아서 돌려준다. 그래서 "생성되는 동안 전달되는지"와 "연결 종료"는
ASGI 앱을 직접 구동하는 stream_over_asgi로 확인한다.
"""

import asyncio
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
import pytest
from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Message, Scope

from app.agent.service import AgentService
from app.api.deps import get_agent_service
from app.config import get_settings
from app.db import get_session
from app.errors import LLMUnavailableError
from app.llm.provider import LLMProvider
from app.llm.types import JsonObject
from app.main import app
from app.models import Message as StoredMessage, MessageRole, Store, SupportTicket
from app.schemas import MessageDeltaEvent, StreamErrorEvent
from tests.factories import create_store
from tests.mock_llm import Chunks, MockGeminiProvider, final_text, function_call

REFUND_QUESTION = "Can I get a refund after 60 days?"
TICKET_CALL = {"question": REFUND_QUESTION, "escalation_reason": "no_knowledge"}
_JSON_OBJECT = TypeAdapter(JsonObject)

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

@dataclass(frozen=True)
class SseEvent:
    name: str
    data: JsonObject

def parse_sse(text: str) -> list[SseEvent]:
    """SSE 본문을 이벤트로 나눈다. 모든 이벤트가 정확히 `event:` 한 줄과 `data:` 한 줄이어야 한다."""
    events: list[SseEvent] = []
    for block in text.split("\n\n"):
        if block == "":
            continue
        lines = block.split("\n")
        assert len(lines) == 2, f"SSE 형식이 깨졌습니다: {block!r}"
        event_line, data_line = lines
        assert event_line.startswith("event: ")
        assert data_line.startswith("data: ")
        events.append(SseEvent(event_line.removeprefix("event: "), _JSON_OBJECT.validate_json(data_line.removeprefix("data: "))))
    return events

def delta_texts(events: list[SseEvent]) -> list[str]:
    return [MessageDeltaEvent.model_validate(e.data).text for e in events if e.name == "message_delta"]

async def start_conversation(client: httpx.AsyncClient, visitor_id: str = "visitor-1") -> str:
    res = await client.post("/api/conversations", json={"visitor_id": visitor_id})
    assert res.status_code == 201
    return res.json()["id"]

async def stream(client: httpx.AsyncClient, conversation_id: str, content: str) -> httpx.Response:
    return await client.post(f"/api/conversations/{conversation_id}/messages/stream", json={"content": content})

async def stored_messages(session: AsyncSession, conversation_id: str) -> list[StoredMessage]:
    result = await session.scalars(
        select(StoredMessage)
        .where(StoredMessage.conversation_id == uuid.UUID(conversation_id))
        .order_by(StoredMessage.created_at)
    )
    return list(result.all())

async def ticket_count(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(SupportTicket)) or 0

@dataclass
class AsgiStream:
    status: int | None = None
    events: list[SseEvent] = field(default_factory=list)

async def stream_over_asgi(
    conversation_id: str,
    content: str,
    *,
    on_event: Callable[[SseEvent], None],
    disconnect: asyncio.Event,
) -> AsgiStream:
    """ASGI 앱을 직접 구동한다. 응답 본문이 오는 즉시 이벤트로 나눠 on_event에 넘기고, disconnect가 set되면 연결을 끊는다."""
    body = json.dumps({"content": content}).encode()
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": f"/api/conversations/{conversation_id}/messages/stream",
        "raw_path": f"/api/conversations/{conversation_id}/messages/stream".encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"test"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
    }
    request_sent = False
    result = AsgiStream()
    pending_text = ""

    async def receive() -> Message:
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        nonlocal pending_text
        if message["type"] == "http.response.start":
            result.status = message["status"]
        elif message["type"] == "http.response.body":
            pending_text += message.get("body", b"").decode()
            while "\n\n" in pending_text:
                block, pending_text = pending_text.split("\n\n", 1)
                for event in parse_sse(block + "\n\n"):
                    result.events.append(event)
                    on_event(event)

    async with asyncio.timeout(10):
        await app(scope, receive, send)
    return result

# --- 1~5. 기본 스트리밍 ------------------------------------------------------------------------------

async def test_response_is_an_event_stream(client: httpx.AsyncClient, store: Store) -> None:
    conversation_id = await start_conversation(client)
    use_agent(MockGeminiProvider([Chunks(["Hello", "!"])]))

    res = await stream(client, conversation_id, "Hi")

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert res.headers["cache-control"] == "no-cache"
    # 프록시가 응답을 모으지 않게 한다.
    assert res.headers["x-accel-buffering"] == "no"

async def test_several_message_delta_events_then_completed(client: httpx.AsyncClient, store: Store) -> None:
    conversation_id = await start_conversation(client)
    use_agent(MockGeminiProvider([Chunks(["Our store", " is open", " from 9 AM to 6 PM."])]))

    res = await stream(client, conversation_id, "What are your business hours?")

    events = parse_sse(res.text)
    assert [e.name for e in events] == ["message_delta", "message_delta", "message_delta", "completed"]
    assert [e.data for e in events[:3]] == [{"text": "Our store"}, {"text": " is open"}, {"text": " from 9 AM to 6 PM."}]

async def test_answer_is_saved_once_as_a_single_message_not_per_chunk(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    conversation_id = await start_conversation(client)
    use_agent(MockGeminiProvider([Chunks(["Yes, ", "we ", "have ", "oat ", "milk."])]))

    await stream(client, conversation_id, "Do you have oat milk?")

    assert [(m.role, m.content) for m in await stored_messages(session, conversation_id)] == [
        (MessageRole.USER, "Do you have oat milk?"),
        (MessageRole.ASSISTANT, "Yes, we have oat milk."),
    ]
    listed = await client.get(f"/api/conversations/{conversation_id}/messages")
    assert [(m["role"], m["content"]) for m in listed.json()] == [
        ("user", "Do you have oat milk?"),
        ("assistant", "Yes, we have oat milk."),
    ]

# --- 실시간 전달 --------------------------------------------------------------------------------------

async def test_deltas_reach_the_client_while_the_model_is_still_generating(
    client: httpx.AsyncClient, store: Store
) -> None:
    """첫 조각은 모델이 두 번째 조각을 만들기 전에 클라이언트에 도착한다.

    Mock은 두 번째 조각 앞에서 멈춰 있고, 클라이언트가 첫 조각을 받은 뒤에야 풀린다.
    응답을 모아서 한꺼번에 보내는 구현이라면 여기서 교착되어 시간 초과로 실패한다.
    """
    conversation_id = await start_conversation(client)
    proceed = asyncio.Event()
    use_agent(MockGeminiProvider([Chunks(["first", proceed, " second"])]))

    def on_event(event: SseEvent) -> None:
        if event.name == "message_delta" and event.data == {"text": "first"}:
            proceed.set()

    result = await stream_over_asgi(conversation_id, "Hi", on_event=on_event, disconnect=asyncio.Event())

    assert result.status == 200
    assert [e.name for e in result.events] == ["message_delta", "message_delta", "completed"]
    assert delta_texts(result.events) == ["first", " second"]

# --- SSE 형식 안전성 ----------------------------------------------------------------------------------

async def test_newlines_and_special_characters_do_not_break_the_event_format(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    conversation_id = await start_conversation(client)
    hostile = ["Line1\nLine2\r\n", 'He said "hi" \\ \u2028 한국어 🙂', "\n\nevent: error\ndata: {}\n\n"]
    use_agent(MockGeminiProvider([Chunks(list(hostile))]))

    res = await stream(client, conversation_id, "Hi")

    # 텍스트 안의 줄바꿈과 가짜 이벤트 문구는 JSON 문자열로 이스케이프되어, 이벤트는 정확히 4개다.
    events = parse_sse(res.text)
    assert [e.name for e in events] == ["message_delta"] * 3 + ["completed"]
    assert delta_texts(events) == hostile
    *_, saved = await stored_messages(session, conversation_id)
    assert saved.content == "".join(hostile).strip()

# --- 6. 답변 도중 실패 ----------------------------------------------------------------------------------

async def test_failure_in_the_middle_of_the_answer_never_saves_a_partial_message(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    conversation_id = await start_conversation(client)
    # 티켓을 만든 뒤 답변을 쓰다가 Gemini가 끊긴다. 일부 텍스트가 이미 나갔어도 저장하지 않는다.
    use_agent(
        MockGeminiProvider(
            [
                function_call("create_support_ticket", **TICKET_CALL),
                Chunks(["I've forwarded ", LLMUnavailableError("AI 서비스에 연결할 수 없습니다.")]),
            ]
        )
    )

    res = await stream(client, conversation_id, REFUND_QUESTION)

    events = parse_sse(res.text)
    assert [e.name for e in events] == ["message_delta", "error"]
    assert StreamErrorEvent.model_validate(events[1].data).code == "LLM_UNAVAILABLE"
    assert [(m.role, m.content) for m in await stored_messages(session, conversation_id)] == [
        (MessageRole.USER, REFUND_QUESTION)
    ]
    # 답변 없는 티켓이 남지 않는다.
    assert await ticket_count(session) == 0

async def test_non_inquiry_is_an_error_event_and_the_answer_is_not_saved(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    conversation_id = await start_conversation(client)
    use_agent(
        MockGeminiProvider(
            [Chunks(["Nice weather."], tool_calls=function_call("reject_not_inquiry").tool_calls)]
        )
    )

    res = await stream(client, conversation_id, "How is the weather?")

    events = parse_sse(res.text)
    assert [e.name for e in events] == ["message_delta", "error"]
    assert events[1].data == {"code": "NOT_STORE_INQUIRY", "message": "매장에 대한 문의만 답변할 수 있습니다."}
    assert [(m.role, m.content) for m in await stored_messages(session, conversation_id)] == [
        (MessageRole.USER, "How is the weather?")
    ]
    assert await ticket_count(session) == 0


async def test_unexpected_error_is_reported_without_internal_details(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    conversation_id = await start_conversation(client)
    use_agent(MockGeminiProvider([RuntimeError("secret internal detail: db password=hunter2")]))

    res = await stream(client, conversation_id, "Hi")

    events = parse_sse(res.text)
    assert [e.name for e in events] == ["error"]
    assert events[0].data == {"code": "INTERNAL_ERROR", "message": "서버 내부 오류가 발생했습니다."}
    assert "hunter2" not in res.text
    assert [m.role for m in await stored_messages(session, conversation_id)] == [MessageRole.USER]

# --- 스트림 시작 전 오류는 일반 오류 응답 ---------------------------------------------------------------

async def test_unknown_conversation_is_a_plain_404_not_a_stream(client: httpx.AsyncClient, store: Store) -> None:
    provider = MockGeminiProvider([Chunks(["never"])])
    use_agent(provider)

    res = await stream(client, str(uuid.uuid4()), "Hi")

    assert res.status_code == 404
    assert res.headers["content-type"].startswith("application/json")
    assert res.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"
    assert provider.requests == []

# --- 7. 연결 종료 -------------------------------------------------------------------------------------

async def test_client_disconnect_stops_generation_and_saves_no_partial_answer(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    conversation_id = await start_conversation(client)
    never = asyncio.Event()
    disconnect = asyncio.Event()
    # 티켓을 만든 뒤 답변 생성 도중(두 번째 조각 앞에서 멈춤)에 고객이 연결을 끊는다.
    provider = MockGeminiProvider(
        [
            function_call("create_support_ticket", **TICKET_CALL),
            Chunks(["I've forwarded ", never, "your question."]),
            final_text("must not be requested"),
        ]
    )
    use_agent(provider)

    def on_event(event: SseEvent) -> None:
        if event.name == "message_delta":
            disconnect.set()

    result = await stream_over_asgi(conversation_id, REFUND_QUESTION, on_event=on_event, disconnect=disconnect)

    # 끊긴 뒤에는 완료도 오류도 보내지 않는다. 진행 중이던 LLM 스트림은 취소되고 다음 요청은 없다.
    assert [e.name for e in result.events] == ["message_delta"]
    assert provider.interrupted_streams == 1
    assert len(provider.requests) == 2
    # 고객 메시지는 남고, 불완전한 답변과 그 답변에 딸린 티켓은 남지 않는다.
    assert [(m.role, m.content) for m in await stored_messages(session, conversation_id)] == [
        (MessageRole.USER, REFUND_QUESTION)
    ]
    assert await ticket_count(session) == 0

# --- 8~10. Tool Calling과 최종 답변 스트리밍 -------------------------------------------------------------

async def test_tool_calling_flow_is_kept_and_only_the_final_answer_is_streamed(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    conversation_id = await start_conversation(client)
    provider = MockGeminiProvider(
        [
            function_call("get_store_profile"),
            function_call("search_knowledge", query="refund 60 days"),
            function_call("create_support_ticket", **TICKET_CALL),
            Chunks(["I've forwarded ", "your question ", "to our staff."]),
        ]
    )
    use_agent(provider)

    res = await stream(client, conversation_id, REFUND_QUESTION)

    events = parse_sse(res.text)
    # Tool 호출과 결과는 사용자에게 보이지 않는다. 이벤트는 최종 답변 조각과 completed뿐이다.
    assert [e.name for e in events] == ["message_delta"] * 3 + ["completed"]
    assert "".join(delta_texts(events)) == "I've forwarded your question to our staff."
    for hidden in ("get_store_profile", "search_knowledge", "create_support_ticket", "ticket_id", "call-"):
        assert hidden not in res.text
    # 기존 Agent 흐름: Tool 3개가 실행되고 티켓이 만들어지며, 답변과 함께 커밋된다.
    assert len(provider.requests) == 4
    assert await ticket_count(session) == 1
    ticket = await session.scalar(select(SupportTicket))
    assert ticket is not None
    assert ticket.question == REFUND_QUESTION
    saved = await stored_messages(session, conversation_id)
    assert [(m.role, m.content) for m in saved] == [
        (MessageRole.USER, REFUND_QUESTION),
        (MessageRole.ASSISTANT, "I've forwarded your question to our staff."),
    ]
    assert ticket.source_message_id == saved[0].id


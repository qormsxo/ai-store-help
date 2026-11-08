"""사장용 문의 관리 API 테스트. 실제 FastAPI 라우터, 서비스, PostgreSQL을 쓴다.

고객 대화에 AI가 답하는 부분만 MockGeminiProvider(가짜)로 바꾼다. 실제 Gemini는 호출하지 않는다.
"""

import uuid
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.service import AgentService
from app.agent.tools import KnowledgeSearchOutput
from app.api.deps import get_agent_service
from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models import (
    KnowledgeEntry,
    Message,
    MessageRole,
    Store,
    SupportTicket,
    TicketStatus,
    utcnow,
)
from app.schemas import MAX_ANSWER_LENGTH
from app.services import knowledge_search_service
from tests.factories import create_conversation, create_message, create_store, create_ticket
from tests.mock_llm import MockGeminiProvider, final_text, function_call, observation

QUESTION = "Are dogs allowed inside?"
ANSWER = "Only service dogs are allowed inside. Pets are welcome on the patio."


@pytest.fixture
async def store(session: AsyncSession) -> Store:
    """API가 응대하는 기본 매장. API 요청이 테스트와 같은 DB 세션을 쓰게 한다."""
    app.dependency_overrides[get_session] = lambda: session
    return await create_store(session, slug=get_settings().default_store_slug, name="Oak & Pine Coffee Roasters")


@dataclass(frozen=True)
class OpenTicket:
    """준비한 티켓의 id들. 서비스가 롤백하면 세션의 ORM 객체가 만료되므로 값으로만 들고 있는다."""

    id: uuid.UUID
    conversation_id: uuid.UUID
    source_message_id: uuid.UUID | None


async def open_ticket(
    session: AsyncSession,
    store: Store,
    question: str = QUESTION,
    customer_text: str = "Do you allow dogs inside?",
    *,
    with_source_message: bool = True,
) -> OpenTicket:
    """AI가 문의를 사장에게 넘긴 뒤의 상태: 고객 질문, AI의 안내 답변, 미처리 티켓.

    서비스가 실패하면 롤백하므로, 준비한 데이터가 함께 사라지지 않게 커밋해 둔다.
    """
    conversation = await create_conversation(session, store)
    message = await create_message(session, conversation, customer_text, MessageRole.USER)
    await create_message(session, conversation, "I've forwarded your question to our staff.", MessageRole.ASSISTANT)
    ticket = await create_ticket(session, conversation, message if with_source_message else None, question)
    await session.commit()
    return OpenTicket(id=ticket.id, conversation_id=conversation.id, source_message_id=ticket.source_message_id)


async def reply(client: httpx.AsyncClient, ticket_id: uuid.UUID, answer: str) -> httpx.Response:
    return await client.post(f"/api/owner/tickets/{ticket_id}/reply", json={"answer": answer})


async def count(session: AsyncSession, model: type[KnowledgeEntry] | type[Message] | type[SupportTicket]) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


async def fresh_ticket(session: AsyncSession, ticket_id: uuid.UUID) -> SupportTicket:
    """DB에 저장된 최신 티켓. 서비스가 롤백하면 세션의 객체가 낡을 수 있어 다시 읽는다."""
    ticket = await session.get(SupportTicket, ticket_id, populate_existing=True)
    assert ticket is not None
    return ticket


async def fresh_knowledge(session: AsyncSession, entry_id: uuid.UUID | str) -> KnowledgeEntry:
    entry = await session.get(KnowledgeEntry, uuid.UUID(str(entry_id)), populate_existing=True)
    assert entry is not None
    return entry


async def messages_of(session: AsyncSession, conversation_id: uuid.UUID) -> list[Message]:
    result = await session.scalars(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at, Message.id)
    )
    return list(result.all())


def error_of(res: httpx.Response) -> dict[str, str]:
    body = res.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    return body["error"]


# --- 목록 조회 ---------------------------------------------------------------------------------


async def test_list_tickets_oldest_first(client: httpx.AsyncClient, session: AsyncSession, store: Store) -> None:
    first = await open_ticket(session, store, "Is there parking?")
    second = await open_ticket(session, store, "Do you sell gift cards?")

    res = await client.get("/api/owner/tickets")

    assert res.status_code == 200
    assert [t["id"] for t in res.json()] == [str(first.id), str(second.id)]
    assert set(res.json()[0]) == {
        "id",
        "status",
        "question",
        "escalation_reason",
        "conversation_id",
        "owner_answer",
        "created_at",
        "resolved_at",
    }
    assert res.json()[0]["question"] == "Is there parking?"
    assert res.json()[0]["status"] == "open"
    assert res.json()[0]["escalation_reason"] == "no_knowledge"


async def test_list_tickets_filters_by_status(client: httpx.AsyncClient, session: AsyncSession, store: Store) -> None:
    open_one = await open_ticket(session, store, "Is there parking?")
    done = await open_ticket(session, store, "Do you sell gift cards?")
    resolved_ticket = await fresh_ticket(session, done.id)
    resolved_ticket.status = TicketStatus.RESOLVED
    resolved_ticket.owner_answer = "Yes."
    resolved_ticket.resolved_at = utcnow()
    await session.commit()

    opened = await client.get("/api/owner/tickets", params={"status": "open"})
    resolved = await client.get("/api/owner/tickets", params={"status": "resolved"})
    everything = await client.get("/api/owner/tickets")

    assert [t["id"] for t in opened.json()] == [str(open_one.id)]
    assert [t["id"] for t in resolved.json()] == [str(done.id)]
    assert resolved.json()[0]["owner_answer"] == "Yes."
    assert len(everything.json()) == 2


async def test_list_tickets_rejects_unknown_status(client: httpx.AsyncClient, store: Store) -> None:
    res = await client.get("/api/owner/tickets", params={"status": "archived"})

    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_list_tickets_is_empty_without_tickets(client: httpx.AsyncClient, store: Store) -> None:
    res = await client.get("/api/owner/tickets")

    assert res.status_code == 200
    assert res.json() == []


async def test_list_excludes_other_store_tickets(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    mine = await open_ticket(session, store)
    other_store = await create_store(session, slug="other-store")
    await open_ticket(session, other_store, "Other store question?")

    res = await client.get("/api/owner/tickets")

    assert [t["id"] for t in res.json()] == [str(mine.id)]


# --- 상세 조회 ---------------------------------------------------------------------------------


async def test_ticket_detail_includes_customer_conversation(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    ticket = await open_ticket(session, store)

    res = await client.get(f"/api/owner/tickets/{ticket.id}")

    assert res.status_code == 200
    body = res.json()
    assert body["id"] == str(ticket.id)
    assert body["question"] == QUESTION
    assert body["source_message_id"] == str(ticket.source_message_id)
    assert body["conversation"]["id"] == str(ticket.conversation_id)
    assert [(m["role"], m["content"]) for m in body["conversation"]["messages"]] == [
        ("user", "Do you allow dogs inside?"),
        ("assistant", "I've forwarded your question to our staff."),
    ]
    assert body["conversation"]["messages"][0]["id"] == body["source_message_id"]


@pytest.mark.parametrize("method", ["get", "reply"])
async def test_unknown_ticket_is_404(client: httpx.AsyncClient, store: Store, method: str) -> None:
    ticket_id = uuid.uuid4()

    res = (
        await client.get(f"/api/owner/tickets/{ticket_id}")
        if method == "get"
        else await reply(client, ticket_id, "Yes.")
    )

    assert res.status_code == 404
    assert error_of(res) == {"code": "TICKET_NOT_FOUND", "message": "문의를 찾을 수 없습니다."}


async def test_other_store_ticket_is_hidden_and_untouched(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    other_store = await create_store(session, slug="other-store")
    other = await open_ticket(session, other_store, "Other store question?")

    detail = await client.get(f"/api/owner/tickets/{other.id}")
    answered = await reply(client, other.id, "Yes.")

    assert detail.status_code == answered.status_code == 404
    assert error_of(answered)["code"] == "TICKET_NOT_FOUND"
    assert await count(session, KnowledgeEntry) == 0
    assert (await fresh_ticket(session, other.id)).status is TicketStatus.OPEN


async def test_malformed_ticket_id_is_validation_error(client: httpx.AsyncClient, store: Store) -> None:
    res = await client.get("/api/owner/tickets/not-a-uuid")

    assert res.status_code == 422


# --- 사장 답변 처리 ----------------------------------------------------------------------------


async def test_reply_saves_knowledge_resolves_ticket_and_messages_customer(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    ticket = await open_ticket(session, store)

    res = await reply(client, ticket.id, f"  {ANSWER}  ")

    assert res.status_code == 200
    body = res.json()
    assert body["ticket"]["status"] == "resolved"
    assert body["ticket"]["owner_answer"] == ANSWER
    assert body["ticket"]["resolved_at"] is not None
    assert body["superseded_knowledge_id"] is None

    # 1. 지식: 원래 질문과 사장 답변, 출처 티켓이 연결된다.
    knowledge = await fresh_knowledge(session, body["knowledge_entry_id"])
    assert (knowledge.store_id, knowledge.question, knowledge.answer, knowledge.is_active) == (
        store.id,
        QUESTION,
        ANSWER,
        True,
    )
    assert knowledge.source_ticket_id == ticket.id

    # 2. 티켓: resolved, 답변과 해결 시각 기록
    saved_ticket = await fresh_ticket(session, ticket.id)
    assert (saved_ticket.status, saved_ticket.owner_answer) == (TicketStatus.RESOLVED, ANSWER)
    assert saved_ticket.resolved_at is not None

    # 3. 고객 대화: 원래 대화에 AI Assistant 메시지로 추가된다.
    *_, last = await messages_of(session, ticket.conversation_id)
    assert (last.role, last.content) == (MessageRole.ASSISTANT, ANSWER)
    assert body["conversation_message"]["id"] == str(last.id)
    assert (body["conversation_message"]["role"], body["conversation_message"]["content"]) == ("assistant", ANSWER)


async def test_customer_sees_owner_answer_in_the_original_conversation(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    ticket = await open_ticket(session, store)

    await reply(client, ticket.id, ANSWER)

    res = await client.get(f"/api/conversations/{ticket.conversation_id}/messages")
    assert [(m["role"], m["content"]) for m in res.json()] == [
        ("user", "Do you allow dogs inside?"),
        ("assistant", "I've forwarded your question to our staff."),
        ("assistant", ANSWER),
    ]


async def test_next_customer_gets_the_owner_answer_through_the_agent(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    """프로젝트의 핵심 순환: 사장이 답하면, 이후 비슷한 질문은 그 지식으로 답한다."""
    ticket = await open_ticket(session, store)
    await reply(client, ticket.id, ANSWER)
    provider = MockGeminiProvider(
        [
            function_call("search_knowledge", query="dogs inside"),
            final_text("Only service dogs are allowed inside, but pets are welcome on the patio."),
        ]
    )
    app.dependency_overrides[get_agent_service] = lambda: AgentService(provider)
    conversation = (await client.post("/api/conversations", json={"visitor_id": "visitor-2"})).json()["id"]

    res = await client.post(
        f"/api/conversations/{conversation}/messages/stream", json={"content": "Can my dog come in?"}
    )

    assert res.status_code == 200
    assert "event: completed\n" in res.text
    found = KnowledgeSearchOutput.model_validate(observation(provider.requests[1]).results[0].content)
    assert [(r.question, r.answer) for r in found.results] == [(QUESTION, ANSWER)]


async def test_second_reply_is_rejected_and_changes_nothing(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    ticket = await open_ticket(session, store)
    first = await reply(client, ticket.id, ANSWER)

    second = await reply(client, ticket.id, "Actually, no dogs at all.")

    assert second.status_code == 409
    assert error_of(second) == {"code": "TICKET_ALREADY_RESOLVED", "message": "이미 답변이 등록된 문의입니다."}
    assert await count(session, KnowledgeEntry) == 1
    assert (await fresh_ticket(session, ticket.id)).owner_answer == ANSWER
    assert [m.content for m in await messages_of(session, ticket.conversation_id)].count(ANSWER) == 1
    assert (await fresh_knowledge(session, first.json()["knowledge_entry_id"])).answer == ANSWER


@pytest.mark.parametrize("answer", ["", "   ", "\n\t", "a" * (MAX_ANSWER_LENGTH + 1)])
async def test_invalid_answer_changes_nothing(
    client: httpx.AsyncClient, session: AsyncSession, store: Store, answer: str
) -> None:
    ticket = await open_ticket(session, store)

    res = await reply(client, ticket.id, answer)

    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"
    assert await count(session, KnowledgeEntry) == 0
    assert (await fresh_ticket(session, ticket.id)).status is TicketStatus.OPEN
    assert len(await messages_of(session, ticket.conversation_id)) == 2


async def test_answer_at_max_length_is_accepted(client: httpx.AsyncClient, session: AsyncSession, store: Store) -> None:
    ticket = await open_ticket(session, store)

    res = await reply(client, ticket.id, "a" * MAX_ANSWER_LENGTH)

    assert res.status_code == 200


async def test_missing_answer_field_is_rejected(client: httpx.AsyncClient, session: AsyncSession, store: Store) -> None:
    ticket = await open_ticket(session, store)

    res = await client.post(f"/api/owner/tickets/{ticket.id}/reply", json={})

    assert res.status_code == 422


# --- 같은 질문의 지식이 이미 있는 경우 --------------------------------------------------------------


async def test_same_question_twice_supersedes_the_older_knowledge(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    # 같은 질문을 한 고객이 둘이라서 티켓이 두 개다.
    first = await open_ticket(session, store)
    second = await open_ticket(session, store)
    first_reply = await reply(client, first.id, "Only service dogs.")

    res = await reply(client, second.id, ANSWER)

    # 두 번째 티켓도 막히지 않고 해결되며, 활성 지식은 최신 답변 하나뿐이다.
    assert res.status_code == 200
    assert res.json()["superseded_knowledge_id"] == first_reply.json()["knowledge_entry_id"]
    old = await fresh_knowledge(session, first_reply.json()["knowledge_entry_id"])
    new = await fresh_knowledge(session, res.json()["knowledge_entry_id"])
    assert (old.is_active, new.is_active) == (False, True)
    assert old.source_ticket_id == first.id
    assert new.source_ticket_id == second.id
    found = await knowledge_search_service.search_knowledge(session, store.id, "dogs inside")
    assert [r.answer for r in found] == [ANSWER]
    assert (await fresh_ticket(session, second.id)).status is TicketStatus.RESOLVED


async def test_question_match_ignores_case_and_spaces(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    first = await open_ticket(session, store, "Are dogs allowed inside?")
    second = await open_ticket(session, store, "ARE   dogs  allowed inside?")
    first_reply = await reply(client, first.id, "Only service dogs.")

    res = await reply(client, second.id, ANSWER)

    assert res.status_code == 200
    assert res.json()["superseded_knowledge_id"] == first_reply.json()["knowledge_entry_id"]


async def test_other_store_knowledge_is_not_superseded(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    other_store = await create_store(session, slug="other-store")
    other_ticket = await open_ticket(session, other_store)
    other_knowledge = KnowledgeEntry(
        store_id=other_store.id, question=QUESTION, answer="Other store answer.", source_ticket_id=other_ticket.id
    )
    session.add(other_knowledge)
    ticket = await open_ticket(session, store)
    await session.commit()

    res = await reply(client, ticket.id, ANSWER)

    assert res.status_code == 200
    assert res.json()["superseded_knowledge_id"] is None
    assert (await fresh_knowledge(session, other_knowledge.id)).is_active is True


# --- 원자성: 중간에 실패하면 아무것도 반영되지 않는다 ------------------------------------------------


async def test_failure_while_saving_the_message_rolls_back_knowledge_and_ticket(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    ticket = await open_ticket(session, store)
    # 'FAIL'로 시작하는 메시지를 저장하려 하면 DB가 오류를 내는 트리거. 지식 저장과 티켓 변경이 이미 끝난 뒤인
    # 마지막 단계(대화 메시지 저장)에서 실제 DB 오류를 만든다. 테스트 트랜잭션과 함께 사라진다.
    await session.execute(
        text(
            "CREATE FUNCTION fail_reply_message() RETURNS trigger LANGUAGE plpgsql AS "
            "$$ BEGIN RAISE EXCEPTION 'simulated failure'; END; $$"
        )
    )
    await session.execute(
        text(
            "CREATE TRIGGER fail_reply_message BEFORE INSERT ON messages FOR EACH ROW "
            "WHEN (NEW.content LIKE 'FAIL%') EXECUTE FUNCTION fail_reply_message()"
        )
    )
    await session.commit()

    res = await reply(client, ticket.id, "FAIL: only service dogs")

    assert res.status_code == 503
    assert error_of(res) == {
        "code": "SERVICE_UNAVAILABLE",
        "message": "답변을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    }
    assert "simulated" not in res.text
    assert await count(session, KnowledgeEntry) == 0
    saved_ticket = await fresh_ticket(session, ticket.id)
    assert (saved_ticket.status, saved_ticket.owner_answer, saved_ticket.resolved_at) == (TicketStatus.OPEN, None, None)
    assert len(await messages_of(session, ticket.conversation_id)) == 2

    # 같은 티켓에 정상 답변을 다시 보내면 처리된다.
    retried = await reply(client, ticket.id, ANSWER)
    assert retried.status_code == 200
    assert await count(session, KnowledgeEntry) == 1


# --- 기타 경계 -------------------------------------------------------------------------------------


async def test_reply_works_when_source_message_is_gone(
    client: httpx.AsyncClient, session: AsyncSession, store: Store
) -> None:
    ticket = await open_ticket(session, store, with_source_message=False)

    detail = await client.get(f"/api/owner/tickets/{ticket.id}")
    res = await reply(client, ticket.id, ANSWER)

    assert detail.json()["source_message_id"] is None
    assert res.status_code == 200

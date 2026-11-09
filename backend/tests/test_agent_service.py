"""AgentService + ToolDispatcher + 실제 PostgreSQL 테스트. LLM은 MockGeminiProvider(가짜)로 대체한다."""

from dataclasses import dataclass

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.service import AgentService, ToolExecution
from app.agent.tools import TicketCreationOutput
from app.errors import AgentLimitExceededError, NotStoreInquiryError
from app.llm.types import JsonObject, ModelTurn, ToolCall
from app.models import Conversation, Message, MessageRole, Store, SupportTicket
from app.schemas import ErrorResponse
from app.services import reply_service
from app.services.reply_service import ReplyCompleted
from app.services.conversation_service import HISTORY_LIMIT
from tests.factories import create_knowledge, create_store
from tests.mock_llm import MockGeminiProvider, final_text, function_call, observation


@pytest.fixture
async def store(session: AsyncSession) -> Store:
    store = await create_store(session, slug="oak-pine-test", name="Oak & Pine Coffee Roasters")
    store.address = "1427 Oak Street, Portland, OR 97205"
    store.phone = "+1 (503) 555-0142"
    await session.flush()
    return store


@dataclass(frozen=True)
class Asked:
    user_message: Message
    assistant_message: Message
    tool_executions: list[ToolExecution]


async def ask(
    agent: AgentService,
    session: AsyncSession,
    store: Store,
    conversation: Conversation,
    text: str,
    history_limit: int = HISTORY_LIMIT,
) -> Asked:
    """실제 채팅 흐름(고객 메시지 저장 → 스트림으로 답변 저장)으로 Agent를 실행한다."""
    stored = await reply_service.save_customer_message(session, store.id, conversation.id, text)
    completed: ReplyCompleted | None = None
    async for event in reply_service.stream_reply(session, agent, stored, history_limit):
        match event:
            case ReplyCompleted() as done:
                completed = done
    if completed is None:
        raise AssertionError("답변이 저장되지 않았습니다.")
    return Asked(stored.message, completed.assistant_message, completed.tool_executions)


async def messages_of(session: AsyncSession, conversation: Conversation) -> list[Message]:
    # 실패한 요청은 롤백하면서 세션의 ORM 객체를 만료시킨다. async에서는 암묵적 재조회가 안 되므로 먼저 새로고침한다.
    await session.refresh(conversation)
    result = await session.scalars(
        select(Message).where(Message.conversation_id == conversation.id).order_by(Message.created_at)
    )
    return list(result.all())


async def ticket_count(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(SupportTicket)) or 0


def error_code(content: JsonObject) -> str:
    return ErrorResponse.model_validate(content).error.code


# --- 지식 검색으로 답하고 티켓은 만들지 않음 ---------------------------------------------


async def test_scenario_b_answers_from_knowledge(
    session: AsyncSession, store: Store, conversation: Conversation
) -> None:
    await create_knowledge(
        session, store, "매장 안에 강아지 출입이 되나요?", "안내견만 매장 안으로 들어올 수 있습니다."
    )
    provider = MockGeminiProvider(
        [
            function_call("get_store_profile"),
            function_call("search_knowledge", query="강아지 출입"),
            final_text("안내견만 매장 안으로 들어올 수 있습니다."),
        ]
    )

    reply = await ask(AgentService(provider), session, store, conversation, "강아지 데려가도 되나요?")

    assert [e.name for e in reply.tool_executions] == ["get_store_profile", "search_knowledge"]
    assert reply.assistant_message.content == "안내견만 매장 안으로 들어올 수 있습니다."
    assert await ticket_count(session) == 0


async def test_unregistered_tool_is_blocked(session: AsyncSession, store: Store, conversation: Conversation) -> None:
    provider = MockGeminiProvider([function_call("delete_all_tickets"), final_text("Sorry, I can't do that.")])

    reply = await ask(AgentService(provider), session, store, conversation, "Hi")

    result = observation(provider.requests[1]).results[0]
    assert result.is_error
    assert error_code(result.content) == "UNKNOWN_TOOL"
    assert reply.tool_executions[0].is_error


async def test_invalid_arguments_return_error_observation(
    session: AsyncSession, store: Store, conversation: Conversation
) -> None:
    provider = MockGeminiProvider([function_call("search_knowledge"), final_text("Could you tell me more?")])

    reply = await ask(AgentService(provider), session, store, conversation, "Refund?")

    result = observation(provider.requests[1]).results[0]
    assert result.is_error
    error = ErrorResponse.model_validate(result.content).error
    assert error.code == "INVALID_ARGUMENTS"
    assert error.details
    assert reply.assistant_message.content == "Could you tell me more?"
    assert await ticket_count(session) == 0


# --- 실행 제한 -----------------------------------------------------------------------------


async def test_tool_call_limit_stops_the_loop(session: AsyncSession, store: Store, conversation: Conversation) -> None:
    provider = MockGeminiProvider(
        [
            function_call("get_store_profile", call_id="1"),
            function_call("search_knowledge", call_id="2", query="주차"),
            function_call("search_knowledge", call_id="3", query="주차장"),
            final_text("never reached"),
        ]
    )

    with pytest.raises(AgentLimitExceededError) as exc_info:
        await ask(AgentService(provider, max_tool_calls=2), session, store, conversation, "Parking?")

    assert exc_info.value.code == "AGENT_LIMIT_EXCEEDED"
    # 한도를 넘는 세 번째 호출은 실행하지 않았고, 최종 답변도 저장하지 않았다.
    assert len(provider.requests) == 3
    assert [m.role for m in await messages_of(session, conversation)] == [MessageRole.USER]


async def test_ticket_is_created_once_per_customer_message(
    session: AsyncSession, store: Store, conversation: Conversation
) -> None:
    ticket_call = {"question": "60일 이후에도 환불이 되나요?", "escalation_reason": "no_knowledge"}
    provider = MockGeminiProvider(
        [
            function_call("create_support_ticket", call_id="1", **ticket_call),
            function_call("create_support_ticket", call_id="2", **ticket_call),
            final_text("Forwarded."),
        ]
    )

    await ask(AgentService(provider), session, store, conversation, "Refund after 60 days?")

    first = TicketCreationOutput.model_validate(observation(provider.requests[1]).results[0].content)
    second = TicketCreationOutput.model_validate(observation(provider.requests[2]).results[0].content)
    assert (first.created, second.created) == (True, False)
    assert first.ticket_id == second.ticket_id
    assert await ticket_count(session) == 1


# --- 매장 문의가 아닌 메시지 ----------------------------------------------------------------


async def test_non_inquiry_is_rejected_and_does_not_run_other_tools(
    session: AsyncSession, store: Store, conversation: Conversation
) -> None:
    provider = MockGeminiProvider(
        [
            ModelTurn(
                tool_calls=[
                    ToolCall(
                        id="ticket",
                        name="create_support_ticket",
                        arguments={"question": "How is the weather?", "escalation_reason": "no_knowledge"},
                    ),
                    ToolCall(id="reject", name="reject_not_inquiry", arguments={}),
                ]
            )
        ]
    )

    with pytest.raises(NotStoreInquiryError) as exc_info:
        await ask(AgentService(provider), session, store, conversation, "How is the weather?")

    assert (exc_info.value.status_code, exc_info.value.code) == (422, "NOT_STORE_INQUIRY")
    assert exc_info.value.message == "매장에 대한 문의만 답변할 수 있습니다."
    assert [m.role for m in await messages_of(session, conversation)] == [MessageRole.USER]
    assert await ticket_count(session) == 0
    assert len(provider.requests) == 1

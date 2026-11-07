"""AgentService + ToolDispatcher + 실제 PostgreSQL 테스트. LLM은 MockGeminiProvider(가짜)로 대체한다."""

from typing import get_args

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.prompts import SYSTEM_INSTRUCTION
from app.agent.service import AgentService
from app.agent.tools import (
    DEFAULT_TOOLS,
    AgentContext,
    CreateSupportTicketArguments,
    KnowledgeSearchOutput,
    NoArguments,
    TicketCreationOutput,
    Tool,
    ToolDispatcher,
)
from app.errors import (
    AgentEmptyResponseError,
    AgentLimitExceededError,
    ConversationClosedError,
    ConversationNotFoundError,
    LLMRateLimitedError,
    LLMTimeoutError,
)
from app.llm.types import JsonObject, ModelTurn, ToolCall, UserTurn
from app.models import (
    Conversation,
    ConversationStatus,
    EscalationReason,
    Message,
    MessageRole,
    Store,
    SupportTicket,
    TicketStatus,
)
from app.schemas import ErrorResponse, StoreProfile
from app.services import chat_service
from app.services.chat_service import ChatResult
from app.services.conversation_service import HISTORY_LIMIT
from tests.factories import create_conversation, create_knowledge, create_message, create_store
from tests.mock_llm import (
    MockGeminiProvider,
    call_result_pairs,
    final_text,
    function_call,
    observation,
    turn_kinds,
)


@pytest.fixture
async def store(session: AsyncSession) -> Store:
    store = await create_store(session, slug="oak-pine-test", name="Oak & Pine Coffee Roasters")
    store.address = "1427 Oak Street, Portland, OR 97205"
    store.phone = "+1 (503) 555-0142"
    await session.flush()
    return store


async def ask(
    agent: AgentService,
    session: AsyncSession,
    store: Store,
    conversation: Conversation,
    text: str,
    history_limit: int = HISTORY_LIMIT,
) -> ChatResult:
    """실제 채팅 흐름(고객 메시지 저장 → Agent 실행 → 답변 저장)으로 Agent를 실행한다."""
    return await chat_service.send_message(session, agent, store.id, conversation.id, text, history_limit)


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


# --- 시나리오 A: 매장 기본 정보로 답변 -------------------------------------------------


async def test_store_profile_tool_is_declared_and_executed(
    session: AsyncSession, store: Store, conversation: Conversation
) -> None:
    provider = MockGeminiProvider([function_call("get_store_profile"), final_text("1427 Oak Street.")])

    await ask(AgentService(provider), session, store, conversation, "What is your store address?")

    first = provider.requests[0]
    assert first.system_instruction == SYSTEM_INSTRUCTION
    assert [tool.name for tool in first.tools] == ["get_store_profile", "search_knowledge", "create_support_ticket"]
    # 인자가 없는 Tool은 parameters 스키마도 없다.
    assert first.tools[0].parameters is None
    assert first.turns == (UserTurn(text="What is your store address?"),)

    result = observation(provider.requests[1]).results[0]
    assert (result.call_id, result.name, result.is_error) == ("call-get_store_profile", "get_store_profile", False)
    profile = StoreProfile.model_validate(result.content)
    assert profile.store.source_id == store.id
    assert "Address: 1427 Oak Street, Portland, OR 97205" in profile.store.content


async def test_scenario_a_answers_from_store_profile_only(
    session: AsyncSession, store: Store, conversation: Conversation
) -> None:
    provider = MockGeminiProvider(
        [function_call("get_store_profile"), final_text("We are at 1427 Oak Street, Portland, OR 97205.")]
    )

    reply = await ask(AgentService(provider), session, store, conversation, "What is your store address?")

    assert reply.assistant_message.content == "We are at 1427 Oak Street, Portland, OR 97205."
    assert [e.name for e in reply.tool_executions] == ["get_store_profile"]
    assert len(provider.requests) == 2
    assert await ticket_count(session) == 0
    saved = await messages_of(session, conversation)
    assert [(m.role, m.content) for m in saved] == [
        (MessageRole.USER, "What is your store address?"),
        (MessageRole.ASSISTANT, "We are at 1427 Oak Street, Portland, OR 97205."),
    ]
    assert saved[1].id == reply.assistant_message.id


# --- 시나리오 B: Knowledge 검색으로 답변 ------------------------------------------------


async def test_search_knowledge_tool_returns_matching_entry(
    session: AsyncSession, store: Store, conversation: Conversation
) -> None:
    entry = await create_knowledge(
        session, store, "Are dogs allowed inside?", "Only service dogs are allowed inside. Pets are welcome on the patio."
    )
    provider = MockGeminiProvider(
        [
            function_call("get_store_profile"),
            function_call("search_knowledge", query="dogs inside"),
            final_text("Only service dogs are allowed inside."),
        ]
    )

    await ask(AgentService(provider), session, store, conversation, "Do you allow dogs inside?")

    result = observation(provider.requests[2]).results[0]
    assert (result.call_id, result.name, result.is_error) == ("call-search_knowledge", "search_knowledge", False)
    output = KnowledgeSearchOutput.model_validate(result.content)
    assert len(output.results) == 1
    assert output.results[0].source_id == entry.id
    assert output.results[0].question == "Are dogs allowed inside?"
    assert output.results[0].answer == "Only service dogs are allowed inside. Pets are welcome on the patio."
    assert output.results[0].relevance_score > 0


async def test_scenario_b_answers_from_knowledge(
    session: AsyncSession, store: Store, conversation: Conversation
) -> None:
    await create_knowledge(
        session, store, "Are dogs allowed inside?", "Only service dogs are allowed inside. Pets are welcome on the patio."
    )
    provider = MockGeminiProvider(
        [
            function_call("get_store_profile"),
            function_call("search_knowledge", query="dogs inside"),
            final_text("Only service dogs are allowed inside, but pets are welcome on the patio."),
        ]
    )

    reply = await ask(AgentService(provider), session, store, conversation, "Do you allow dogs inside?")

    assert [e.name for e in reply.tool_executions] == ["get_store_profile", "search_knowledge"]
    assert reply.assistant_message.content == "Only service dogs are allowed inside, but pets are welcome on the patio."
    assert await ticket_count(session) == 0


# --- 시나리오 C: 근거 없음 → 티켓 -------------------------------------------------------


async def test_scenario_c_runs_three_tools_in_order_and_creates_ticket(
    session: AsyncSession, store: Store, conversation: Conversation
) -> None:
    provider = MockGeminiProvider(
        [
            function_call("get_store_profile"),
            function_call("search_knowledge", query="refund after 60 days"),
            function_call(
                "create_support_ticket",
                question="Can I get a refund after 60 days?",
                escalation_reason="no_knowledge",
            ),
            final_text("I've forwarded your question to our staff. They will get back to you."),
        ]
    )

    reply = await ask(AgentService(provider), session, store, conversation, "Can I get a refund after 60 days?")

    # Mock 응답 1~3번은 Function Call, 4번은 최종 텍스트. 모두 소비되었고 LLM 호출은 정확히 4번이다.
    assert provider.responses == []
    assert len(provider.requests) == 4
    assert [e.name for e in reply.tool_executions] == [
        "get_store_profile",
        "search_knowledge",
        "create_support_ticket",
    ]
    assert not any(e.is_error for e in reply.tool_executions)

    # 각 Tool의 결과가 바로 다음 LLM 호출에 Observation으로 전달되었다.
    assert observation(provider.requests[1]).results[0].name == "get_store_profile"
    search = observation(provider.requests[2]).results[0]
    assert search.name == "search_knowledge"
    assert KnowledgeSearchOutput.model_validate(search.content).results == []
    ticket_result = observation(provider.requests[3]).results[0]
    assert ticket_result.name == "create_support_ticket"
    ticket_output = TicketCreationOutput.model_validate(ticket_result.content)
    assert ticket_output.created is True
    assert ticket_output.status is TicketStatus.OPEN

    # 티켓은 이번 고객 메시지와 현재 대화에 연결된다(LLM이 넘기지 않은 값을 Backend가 채운다).
    ticket = await session.scalar(select(SupportTicket))
    assert ticket is not None
    assert ticket.id == ticket_output.ticket_id
    user_message, assistant_message = await messages_of(session, conversation)
    assert (ticket.store_id, ticket.conversation_id, ticket.source_message_id) == (
        store.id,
        conversation.id,
        user_message.id,
    )
    assert ticket.question == "Can I get a refund after 60 days?"
    assert ticket.escalation_reason is EscalationReason.NO_KNOWLEDGE
    assert user_message.content == "Can I get a refund after 60 days?"
    assert assistant_message.content == reply.assistant_message.content


async def test_context_is_kept_across_tool_rounds(
    session: AsyncSession, store: Store, conversation: Conversation
) -> None:
    scripted = [
        function_call("get_store_profile"),
        function_call("search_knowledge", query="refund after 60 days"),
        function_call("create_support_ticket", question="Refund after 60 days?", escalation_reason="no_knowledge"),
        final_text("Forwarded to our staff."),
    ]
    provider = MockGeminiProvider(list(scripted))

    await ask(AgentService(provider), session, store, conversation, "Can I get a refund after 60 days?")

    # 매 호출은 이전 호출의 Context 전체에 (모델 Function Call, Observation)을 덧붙인 것이다.
    for index in range(1, 4):
        previous, current = provider.requests[index - 1], provider.requests[index]
        assert current.turns[: len(previous.turns)] == previous.turns
        assert current.turns[len(previous.turns)] == scripted[index - 1]
    assert turn_kinds(provider.requests[3].turns) == [
        "user",
        "model:get_store_profile",
        "observation",
        "model:search_knowledge",
        "observation",
        "model:create_support_ticket",
        "observation",
    ]
    # Observation은 짝이 되는 Function Call과 id, 이름이 같다.
    pairs = call_result_pairs(provider.requests[3].turns)
    assert [(call.id, call.name) for call, _ in pairs] == [(result.call_id, result.name) for _, result in pairs]
    assert len(pairs) == 3


async def test_function_call_arguments_reach_the_service(
    session: AsyncSession, store: Store, conversation: Conversation
) -> None:
    await create_knowledge(session, store, "Is parking available?", "Free parking behind the shop.")
    provider = MockGeminiProvider(
        [
            function_call("search_knowledge", query="  parking  "),
            function_call("create_support_ticket", question="  Is there EV charging?  ", escalation_reason="insufficient_knowledge"),
            final_text("Forwarded."),
        ]
    )

    await ask(AgentService(provider), session, store, conversation, "Parking with EV charging?")

    # query 앞뒤 공백은 Pydantic 검증에서 정리된 뒤 검색에 쓰였다.
    search = KnowledgeSearchOutput.model_validate(observation(provider.requests[1]).results[0].content)
    assert [r.question for r in search.results] == ["Is parking available?"]
    ticket = await session.scalar(select(SupportTicket))
    assert ticket is not None
    assert ticket.question == "Is there EV charging?"
    assert ticket.escalation_reason is EscalationReason.INSUFFICIENT_KNOWLEDGE


async def test_escalation_reason_values_match_enum() -> None:
    field = CreateSupportTicketArguments.model_fields["escalation_reason"]
    assert set(get_args(field.annotation)) == {reason.value for reason in EscalationReason}


async def test_ticket_question_fits_the_knowledge_question_column() -> None:
    """티켓의 질문은 사장이 답하면 KnowledgeEntry.question(String(500))이 된다. 더 길면 그 티켓은 해결할 수 없다."""
    longest = 500
    for length, valid in ((longest, True), (longest + 1, False)):
        arguments = {"question": "q" * length, "escalation_reason": "no_knowledge"}
        if valid:
            CreateSupportTicketArguments.model_validate(arguments)
        else:
            with pytest.raises(ValidationError):
                CreateSupportTicketArguments.model_validate(arguments)


async def test_unregistered_tool_is_blocked(session: AsyncSession, store: Store, conversation: Conversation) -> None:
    provider = MockGeminiProvider([function_call("delete_all_tickets"), final_text("Sorry, I can't do that.")])

    reply = await ask(AgentService(provider), session, store, conversation, "Hi")

    result = observation(provider.requests[1]).results[0]
    assert result.is_error
    assert error_code(result.content) == "UNKNOWN_TOOL"
    assert reply.tool_executions[0].is_error


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("search_knowledge", {}),
        ("search_knowledge", {"query": "   "}),
        ("search_knowledge", {"query": 123}),
        ("create_support_ticket", {"question": "Refund?", "escalation_reason": "angry_customer"}),
        ("create_support_ticket", {"escalation_reason": "no_knowledge"}),
    ],
)
async def test_invalid_arguments_return_error_observation(
    session: AsyncSession, store: Store, conversation: Conversation, name: str, arguments: dict[str, str | int]
) -> None:
    provider = MockGeminiProvider([function_call(name, **arguments), final_text("Could you tell me more?")])

    reply = await ask(AgentService(provider), session, store, conversation, "Refund?")

    result = observation(provider.requests[1]).results[0]
    assert result.is_error
    error = ErrorResponse.model_validate(result.content).error
    assert error.code == "INVALID_ARGUMENTS"
    assert error.details
    # 잘못된 호출 뒤에도 실행은 계속되고, LLM이 다음 행동을 정한다.
    assert reply.assistant_message.content == "Could you tell me more?"
    assert await ticket_count(session) == 0


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("get_store_profile", {"store_id": "00000000-0000-0000-0000-000000000000"}),
        ("search_knowledge", {"query": "parking", "store_id": "00000000-0000-0000-0000-000000000000"}),
        (
            "create_support_ticket",
            {
                "question": "Refund?",
                "escalation_reason": "no_knowledge",
                "conversation_id": "00000000-0000-0000-0000-000000000000",
                "source_message_id": "00000000-0000-0000-0000-000000000000",
            },
        ),
    ],
)
async def test_llm_cannot_pass_internal_ids(
    session: AsyncSession, store: Store, conversation: Conversation, name: str, arguments: dict[str, str]
) -> None:
    provider = MockGeminiProvider([function_call(name, **arguments), final_text("OK")])

    await ask(AgentService(provider), session, store, conversation, "Refund?")

    result = observation(provider.requests[1]).results[0]
    assert error_code(result.content) == "INVALID_ARGUMENTS"
    assert await ticket_count(session) == 0


async def test_retry_after_invalid_arguments_succeeds(
    session: AsyncSession, store: Store, conversation: Conversation
) -> None:
    await create_knowledge(session, store, "Is parking available?", "Free parking behind the shop.")
    provider = MockGeminiProvider(
        [
            function_call("search_knowledge", call_id="bad"),
            function_call("search_knowledge", call_id="good", query="parking"),
            final_text("Free parking behind the shop."),
        ]
    )

    reply = await ask(AgentService(provider), session, store, conversation, "Where do I park?")

    assert [(e.name, e.is_error) for e in reply.tool_executions] == [
        ("search_knowledge", True),
        ("search_knowledge", False),
    ]


# --- 실행 제한 -----------------------------------------------------------------------------


async def test_tool_call_limit_stops_the_loop(session: AsyncSession, store: Store, conversation: Conversation) -> None:
    provider = MockGeminiProvider(
        [
            function_call("get_store_profile", call_id="1"),
            function_call("search_knowledge", call_id="2", query="parking"),
            function_call("search_knowledge", call_id="3", query="parking lot"),
            final_text("never reached"),
        ]
    )

    with pytest.raises(AgentLimitExceededError) as exc_info:
        await ask(AgentService(provider, max_tool_calls=2), session, store, conversation, "Parking?")

    assert exc_info.value.code == "AGENT_LIMIT_EXCEEDED"
    # 한도를 넘는 세 번째 호출은 실행하지 않았고, 최종 답변도 저장하지 않았다.
    assert len(provider.requests) == 3
    assert [m.role for m in await messages_of(session, conversation)] == [MessageRole.USER]


async def test_parallel_calls_count_toward_the_limit(
    session: AsyncSession, store: Store, conversation: Conversation
) -> None:
    provider = MockGeminiProvider(
        [
            ModelTurn(
                tool_calls=[
                    ToolCall(id="1", name="get_store_profile", arguments={}),
                    ToolCall(id="2", name="search_knowledge", arguments={"query": "parking"}),
                    ToolCall(id="3", name="search_knowledge", arguments={"query": "garage"}),
                ]
            )
        ]
    )

    with pytest.raises(AgentLimitExceededError):
        await ask(AgentService(provider, max_tool_calls=2), session, store, conversation, "Parking?")


async def test_llm_round_limit_prevents_endless_loop(
    session: AsyncSession, store: Store, conversation: Conversation
) -> None:
    # 매번 같은 Tool만 부르는 모델. 라운드 제한이 없으면 끝나지 않는다.
    provider = MockGeminiProvider([function_call("get_store_profile", call_id=str(i)) for i in range(10)])

    with pytest.raises(AgentLimitExceededError):
        await ask(AgentService(provider, max_llm_rounds=3), session, store, conversation, "Hi")

    assert len(provider.requests) == 3


async def test_response_without_call_or_text_is_an_error(
    session: AsyncSession, store: Store, conversation: Conversation
) -> None:
    provider = MockGeminiProvider([ModelTurn(text="  ", finish_reason="MAX_TOKENS")])

    with pytest.raises(AgentEmptyResponseError) as exc_info:
        await ask(AgentService(provider), session, store, conversation, "Hi")

    assert exc_info.value.code == "AGENT_EMPTY_RESPONSE"


# --- Tool 실패와 LLM 오류 -----------------------------------------------------------------


async def _broken_query(context: AgentContext, _: NoArguments) -> NoArguments:
    await context.session.execute(text("SELECT 1 / 0"))
    return NoArguments()


async def test_tool_failure_becomes_error_observation(
    session: AsyncSession, store: Store, conversation: Conversation
) -> None:
    broken = Tool(name="broken_tool", description="Always fails.", arguments_model=NoArguments, handler=_broken_query)
    dispatcher = ToolDispatcher([*DEFAULT_TOOLS, broken])
    provider = MockGeminiProvider(
        [function_call("broken_tool"), function_call("get_store_profile"), final_text("We are at 1427 Oak Street.")]
    )

    reply = await ask(AgentService(provider, dispatcher), session, store, conversation, "Address?")

    failed = observation(provider.requests[1]).results[0]
    assert failed.is_error
    assert error_code(failed.content) == "TOOL_FAILED"
    # 내부 오류 내용(SQL, 예외 메시지)은 LLM에 전달하지 않는다.
    assert "division" not in str(failed.content).lower()
    # savepoint 덕분에 DB 오류 뒤에도 같은 세션으로 다음 Tool과 메시지 저장이 된다.
    assert observation(provider.requests[2]).results[0].is_error is False
    assert [m.role for m in await messages_of(session, conversation)] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert reply.assistant_message.content == "We are at 1427 Oak Street."


async def test_llm_timeout_propagates(session: AsyncSession, store: Store, conversation: Conversation) -> None:
    provider = MockGeminiProvider([LLMTimeoutError("timeout")])

    with pytest.raises(LLMTimeoutError) as exc_info:
        await ask(AgentService(provider), session, store, conversation, "Hi")

    assert exc_info.value.status_code == 504


async def test_llm_rate_limit_in_later_round_propagates(
    session: AsyncSession, store: Store, conversation: Conversation
) -> None:
    provider = MockGeminiProvider([function_call("get_store_profile"), LLMRateLimitedError("rate limited")])

    with pytest.raises(LLMRateLimitedError) as exc_info:
        await ask(AgentService(provider), session, store, conversation, "Hi")

    assert exc_info.value.status_code == 429
    assert [m.role for m in await messages_of(session, conversation)] == [MessageRole.USER]


# --- 대화 문맥 -----------------------------------------------------------------------------


async def test_previous_messages_are_sent_as_context(
    session: AsyncSession, store: Store, conversation: Conversation
) -> None:
    await create_message(session, conversation, "Do you have oat milk?", MessageRole.USER)
    await create_message(session, conversation, "Yes, we do.", MessageRole.ASSISTANT)
    await create_message(session, conversation, "internal note", MessageRole.SYSTEM)
    provider = MockGeminiProvider([final_text("It is $0.50 extra.")])

    await ask(AgentService(provider), session, store, conversation, "Is it extra?")

    assert provider.requests[0].turns == (
        UserTurn(text="Do you have oat milk?"),
        ModelTurn(text="Yes, we do."),
        UserTurn(text="Is it extra?"),
    )


async def test_history_is_limited_to_recent_messages(
    session: AsyncSession, store: Store, conversation: Conversation
) -> None:
    for number in range(5):
        await create_message(session, conversation, f"question {number}", MessageRole.USER)
    provider = MockGeminiProvider([final_text("OK")])

    await ask(AgentService(provider), session, store, conversation, "latest", history_limit=2)

    assert provider.requests[0].turns == (
        UserTurn(text="question 3"),
        UserTurn(text="question 4"),
        UserTurn(text="latest"),
    )


async def test_conversations_do_not_share_context(session: AsyncSession, store: Store) -> None:
    first = await create_conversation(session, store, visitor_id="visitor-a")
    second = await create_conversation(session, store, visitor_id="visitor-b")
    await create_message(session, first, "A: my order number is 1234", MessageRole.USER)
    await create_message(session, second, "B: hello", MessageRole.USER)

    first_provider = MockGeminiProvider([final_text("A reply")])
    await ask(AgentService(first_provider), session, store, first, "A: follow-up")
    second_provider = MockGeminiProvider([final_text("B reply")])
    await ask(AgentService(second_provider), session, store, second, "B: follow-up")

    assert first_provider.requests[0].turns == (
        UserTurn(text="A: my order number is 1234"),
        UserTurn(text="A: follow-up"),
    )
    # 두 번째 대화에는 첫 번째 대화의 기록과 방금 저장된 답변이 들어가지 않는다.
    assert second_provider.requests[0].turns == (UserTurn(text="B: hello"), UserTurn(text="B: follow-up"))


async def test_ticket_is_created_once_per_customer_message(
    session: AsyncSession, store: Store, conversation: Conversation
) -> None:
    ticket_call = {"question": "Refund after 60 days?", "escalation_reason": "no_knowledge"}
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


async def test_other_store_conversation_is_not_found(session: AsyncSession, store: Store) -> None:
    other_store = await create_store(session, slug="other-store")
    other_conversation = await create_conversation(session, other_store)
    provider = MockGeminiProvider([final_text("never")])

    with pytest.raises(ConversationNotFoundError):
        await ask(AgentService(provider), session, store, other_conversation, "Hi")

    assert provider.requests == []


async def test_closed_conversation_is_rejected(
    session: AsyncSession, store: Store, conversation: Conversation
) -> None:
    conversation.status = ConversationStatus.CLOSED
    await session.flush()
    provider = MockGeminiProvider([final_text("never")])

    with pytest.raises(ConversationClosedError):
        await ask(AgentService(provider), session, store, conversation, "Hi")

    assert provider.requests == []

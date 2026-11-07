import logging
from collections.abc import AsyncGenerator, Sequence
from contextlib import aclosing

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.prompts import SYSTEM_INSTRUCTION
from app.agent.tools import AgentContext, ToolDispatcher
from app.errors import AgentEmptyResponseError, AgentLimitExceededError
from app.llm.provider import LLMProvider
from app.llm.types import ModelTurn, StreamItem, TextDelta, ToolDefinition, ToolResultTurn, Turn, UserTurn
from app.models import Conversation, Message, MessageRole

logger = logging.getLogger(__name__)

# 시나리오 C(get_store_profile → search_knowledge → create_support_ticket → 최종 답변)는 LLM 호출 4번,
# Tool 호출 3번이다. 인자를 한 번 고쳐 다시 부를 여유를 두고, 그 이상은 반복으로 보고 멈춘다.
MAX_LLM_ROUNDS = 6
MAX_TOOL_CALLS = 6

# 한 요청에서 앞 라운드가 이미 텍스트를 내보냈는데 다음 라운드도 텍스트를 내보낼 때 둘 사이에 넣는 구분.
_ROUND_SEPARATOR = "\n\n"


class ToolExecution(BaseModel):
    name: str
    is_error: bool


class AgentReply(BaseModel):
    content: str
    # 이번 요청에서 실행한 Tool, 실행 순서대로.
    tool_executions: list[ToolExecution]


class AgentService:
    """저장된 고객 메시지 하나에 답한다. LLM이 Tool을 고르고, 이 클래스는 실행과 반복만 맡는다.

    메시지 저장과 커밋은 하지 않는다(ChatService가 한다). Tool이 만든 티켓만 세션에 쌓이고, 호출하는 쪽이
    답변 저장과 함께 커밋하거나 실패하면 롤백한다. Tool 호출과 Observation은 이번 요청의 실행 Context(turns)에만
    있고 요청이 끝나면 버린다. DB의 대화 메시지에는 들어가지 않는다.

    reply와 reply_stream은 같은 반복문(_events)을 쓴다. 다른 점은 LLM 호출뿐이다. reply는 응답 전체를 받고,
    reply_stream은 Gemini Streaming으로 텍스트를 받는 대로 TextDelta를 내보낸다.
    """

    def __init__(
        self,
        provider: LLMProvider,
        dispatcher: ToolDispatcher | None = None,
        *,
        max_llm_rounds: int = MAX_LLM_ROUNDS,
        max_tool_calls: int = MAX_TOOL_CALLS,
    ) -> None:
        self._provider = provider
        self._dispatcher = dispatcher or ToolDispatcher()
        self._max_llm_rounds = max_llm_rounds
        self._max_tool_calls = max_tool_calls

    async def reply(
        self,
        session: AsyncSession,
        conversation: Conversation,
        user_message: Message,
        history: Sequence[Message],
    ) -> AgentReply:
        """user_message에 답한다. history는 user_message 이전의 같은 대화 메시지(오래된 순)다.

        store_id, conversation_id, visitor_id는 conversation에서, source_message_id는 user_message에서 Backend가
        Context에 넣는다. Tool 인자로는 LLM이 정할 수 없다.
        """
        context, turns = self._start(session, conversation, user_message, history)
        async with aclosing(self._events(context, turns, stream=False)) as events:
            async for event in events:
                match event:
                    case AgentReply():
                        return event
                    case TextDelta():
                        pass
        raise AssertionError("_events는 항상 AgentReply로 끝난다.")

    async def reply_stream(
        self,
        session: AsyncSession,
        conversation: Conversation,
        user_message: Message,
        history: Sequence[Message],
    ) -> AsyncGenerator[TextDelta | AgentReply]:
        """reply와 같지만 최종 답변 텍스트를 생성되는 대로 TextDelta로 내보내고, 마지막에 AgentReply를 낸다.

        Tool 호출과 그 결과는 TextDelta로 나오지 않는다. 내보낸 TextDelta를 이어 붙이면 AgentReply.content와 같다
        (앞뒤 공백만 다르다). 호출하는 쪽이 중간에 멈추면(고객 연결 종료) 이후의 LLM 호출과 Tool 실행은 일어나지 않는다.
        """
        context, turns = self._start(session, conversation, user_message, history)
        # 호출하는 쪽이 중간에 닫으면 아래 단계(LLM 스트림)까지 바로 닫히도록 aclosing을 쓴다.
        async with aclosing(self._events(context, turns, stream=True)) as events:
            async for event in events:
                yield event

    def _start(
        self,
        session: AsyncSession,
        conversation: Conversation,
        user_message: Message,
        history: Sequence[Message],
    ) -> tuple[AgentContext, list[Turn]]:
        context = AgentContext(session=session, conversation=conversation, source_message_id=user_message.id)
        # 실행 Context는 요청마다 새로 만든다. 다른 대화의 기록이 섞일 수 있는 공유 상태가 없다.
        turns: list[Turn] = [_to_turn(message) for message in history]
        turns.append(UserTurn(text=user_message.content))
        return context, turns

    async def _events(
        self, context: AgentContext, turns: list[Turn], *, stream: bool
    ) -> AsyncGenerator[TextDelta | AgentReply]:
        definitions = self._dispatcher.definitions()
        executions: list[ToolExecution] = []
        # 스트리밍으로 고객에게 이미 보낸 텍스트. 저장하는 답변은 화면에 보인 것과 같아야 하므로 이것을 쓴다.
        shown: list[str] = []

        for llm_round in range(1, self._max_llm_rounds + 1):
            model_turn: ModelTurn | None = None
            round_started_text = False
            async with aclosing(self._llm_round(turns, definitions, stream=stream)) as items:
                async for item in items:
                    match item:
                        case TextDelta():
                            if shown and not round_started_text:
                                shown.append(_ROUND_SEPARATOR)
                                yield TextDelta(text=_ROUND_SEPARATOR)
                            round_started_text = True
                            shown.append(item.text)
                            yield item
                        case ModelTurn():
                            model_turn = item
            if model_turn is None:
                raise AgentEmptyResponseError("AI가 답변을 만들지 못했습니다. 다시 시도해 주세요.")
            # Function Call이 담긴 모델 응답도 그대로 Context에 남겨야 다음 요청에서 결과와 짝이 맞는다.
            turns.append(model_turn)

            if not model_turn.tool_calls:
                if model_turn.text and model_turn.text.strip():
                    logger.info(
                        "Agent finished: conversation=%s llm_rounds=%d tools=%s",
                        context.conversation.id,
                        llm_round,
                        [execution.name for execution in executions],
                    )
                    content = "".join(shown).strip() if shown else model_turn.text.strip()
                    yield AgentReply(content=content, tool_executions=executions)
                    return
                logger.error(
                    "LLM returned neither a tool call nor text: conversation=%s finish_reason=%s",
                    context.conversation.id,
                    model_turn.finish_reason,
                )
                raise AgentEmptyResponseError("AI가 답변을 만들지 못했습니다. 다시 시도해 주세요.")

            if len(executions) + len(model_turn.tool_calls) > self._max_tool_calls:
                logger.error(
                    "Tool call limit (%d) exceeded: conversation=%s tools=%s",
                    self._max_tool_calls,
                    context.conversation.id,
                    [execution.name for execution in executions] + [call.name for call in model_turn.tool_calls],
                )
                raise AgentLimitExceededError("AI가 답변을 정리하지 못했습니다. 다시 시도해 주세요.")

            results = [await self._dispatcher.dispatch(context, call) for call in model_turn.tool_calls]
            executions += [ToolExecution(name=result.name, is_error=result.is_error) for result in results]
            turns.append(ToolResultTurn(results=results))

        logger.error(
            "LLM round limit (%d) exceeded: conversation=%s", self._max_llm_rounds, context.conversation.id
        )
        raise AgentLimitExceededError("AI가 답변을 정리하지 못했습니다. 다시 시도해 주세요.")

    def _llm_round(
        self, turns: list[Turn], definitions: Sequence[ToolDefinition], *, stream: bool
    ) -> AsyncGenerator[StreamItem]:
        if stream:
            return self._provider.generate_stream(system_instruction=SYSTEM_INSTRUCTION, turns=turns, tools=definitions)
        return self._generate_once(turns, definitions)

    async def _generate_once(
        self, turns: list[Turn], definitions: Sequence[ToolDefinition]
    ) -> AsyncGenerator[StreamItem]:
        """스트리밍하지 않는 호출을 "ModelTurn 하나짜리 스트림"으로 맞춘다. 반복문이 두 방식을 구분하지 않게 한다."""
        yield await self._provider.generate(system_instruction=SYSTEM_INSTRUCTION, turns=turns, tools=definitions)


def _to_turn(message: Message) -> Turn:
    if message.role is MessageRole.USER:
        return UserTurn(text=message.content)
    return ModelTurn(text=message.content)

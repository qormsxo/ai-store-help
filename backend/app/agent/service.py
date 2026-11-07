import logging
import uuid

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.prompts import SYSTEM_INSTRUCTION
from app.agent.tools import AgentContext, ToolDispatcher
from app.errors import AgentEmptyResponseError, AgentLimitExceededError
from app.llm.provider import LLMProvider
from app.llm.types import ModelTurn, ToolResultTurn, Turn, UserTurn
from app.models import Message, MessageRole
from app.services import conversation_service

logger = logging.getLogger(__name__)

# 시나리오 C(get_store_profile → search_knowledge → create_support_ticket → 최종 답변)는 LLM 호출 4번,
# Tool 호출 3번이다. 인자를 한 번 고쳐 다시 부를 여유를 두고, 그 이상은 반복으로 보고 멈춘다.
MAX_LLM_ROUNDS = 6
MAX_TOOL_CALLS = 6
# Agent에 넘기는 이전 대화 메시지 수.
HISTORY_LIMIT = 20


class ToolExecution(BaseModel):
    name: str
    is_error: bool


class AgentReply(BaseModel):
    message_id: uuid.UUID
    content: str
    # 이번 요청에서 실행한 Tool, 실행 순서대로.
    tool_executions: list[ToolExecution]


class AgentService:
    """고객 메시지 하나에 답한다. LLM이 Tool을 고르고, 이 클래스는 실행과 반복만 맡는다.

    DB에 저장되는 것은 고객 메시지와 최종 답변(그리고 Tool이 만든 티켓)뿐이다. Tool 호출과 Observation은
    이번 요청의 실행 Context(turns)에만 있고, 요청이 끝나면 버린다.
    커밋은 호출하는 쪽이 한다. 실패하면 롤백해서 고객 메시지와 티켓이 반쯤 저장되지 않게 한다.
    """

    def __init__(
        self,
        provider: LLMProvider,
        dispatcher: ToolDispatcher | None = None,
        *,
        max_llm_rounds: int = MAX_LLM_ROUNDS,
        max_tool_calls: int = MAX_TOOL_CALLS,
        history_limit: int = HISTORY_LIMIT,
    ) -> None:
        self._provider = provider
        self._dispatcher = dispatcher or ToolDispatcher()
        self._max_llm_rounds = max_llm_rounds
        self._max_tool_calls = max_tool_calls
        self._history_limit = history_limit

    async def reply(
        self, session: AsyncSession, store_id: uuid.UUID, conversation_id: uuid.UUID, customer_message: str
    ) -> AgentReply:
        conversation = await conversation_service.get_open_conversation(session, store_id, conversation_id)
        history = await conversation_service.get_recent_dialogue(session, conversation, self._history_limit)
        user_message = await conversation_service.add_message(
            session, conversation, MessageRole.USER, customer_message
        )
        context = AgentContext(session=session, conversation=conversation, source_message_id=user_message.id)

        # 실행 Context는 요청마다 새로 만든다. 다른 대화의 기록이 섞일 수 있는 공유 상태가 없다.
        turns: list[Turn] = [_to_turn(message) for message in history]
        turns.append(UserTurn(text=customer_message))
        answer, executions = await self._run(context, turns)

        reply_message = await conversation_service.add_message(session, conversation, MessageRole.ASSISTANT, answer)
        return AgentReply(message_id=reply_message.id, content=answer, tool_executions=executions)

    async def _run(self, context: AgentContext, turns: list[Turn]) -> tuple[str, list[ToolExecution]]:
        definitions = self._dispatcher.definitions()
        executions: list[ToolExecution] = []

        for llm_round in range(1, self._max_llm_rounds + 1):
            model_turn = await self._provider.generate(
                system_instruction=SYSTEM_INSTRUCTION, turns=turns, tools=definitions
            )
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
                    return model_turn.text.strip(), executions
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


def _to_turn(message: Message) -> Turn:
    if message.role is MessageRole.USER:
        return UserTurn(text=message.content)
    return ModelTurn(text=message.content)

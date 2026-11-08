import logging
from collections.abc import AsyncGenerator, Sequence
from contextlib import aclosing

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.prompts import SYSTEM_INSTRUCTION
from app.agent.tools import AgentContext, ToolDispatcher
from app.errors import AgentEmptyResponseError, AgentLimitExceededError, NotStoreInquiryError
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
    """실행이 끝난 도구의 이름과 실패 여부. 결과 본문이 아니고, 손님 화면에도 안 나간다."""

    name: str
    is_error: bool


class AgentReply(BaseModel):
    content: str
    # 이번 요청에서 실행한 Tool, 실행 순서대로.
    tool_executions: list[ToolExecution]


class AgentService:
    """저장된 고객 메시지 하나에 답한다. LLM이 Tool을 고르고, 이 클래스는 실행과 반복만 맡는다.

    메시지 저장과 커밋은 하지 않는다(reply_service가 한다). Tool이 만든 티켓만 세션에 쌓이고, 호출하는 쪽이
    답변 저장과 함께 커밋하거나 실패하면 롤백한다. Tool 호출과 Observation은 이번 요청의 실행 Context(turns)에만
    있고 요청이 끝나면 버린다. DB의 대화 메시지에는 들어가지 않는다.

    고객 화면은 reply_stream만 쓴다. Gemini Streaming으로 텍스트를 받는 대로 TextDelta를 내보낸다.
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

    async def reply_stream(
        self,
        session: AsyncSession,
        conversation: Conversation,
        user_message: Message,
        history: Sequence[Message],
    ) -> AsyncGenerator[TextDelta | AgentReply]:
        """최종 답변 텍스트를 생성되는 대로 TextDelta로 내보내고, 마지막에 AgentReply를 낸다.

        Tool 호출과 그 결과는 TextDelta로 나오지 않는다. 내보낸 TextDelta를 이어 붙이면 AgentReply.content와 같다
        (앞뒤 공백만 다르다). 호출하는 쪽이 중간에 멈추면(고객 연결 종료) 이후의 LLM 호출과 Tool 실행은 일어나지 않는다.
        """
        context, turns = self._start(session, conversation, user_message, history)
        # 호출하는 쪽이 중간에 닫으면 아래 단계(LLM 스트림)까지 바로 닫히도록 aclosing을 쓴다.
        async with aclosing(self._events(context, turns)) as events:
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
        # history에는 이번 질문이 없다. 이전 글만 Turn으로 바꾼 뒤 이번 질문을 맨 끝에 붙인다.
        turns: list[Turn] = [_to_turn(message) for message in history]
        turns.append(UserTurn(text=user_message.content))
        return context, turns

    async def _events(self, context: AgentContext, turns: list[Turn]) -> AsyncGenerator[TextDelta | AgentReply]:
        definitions = self._dispatcher.definitions()
        # 이번 요청에서 이미 실행한 도구. 마지막 AgentReply에 순서대로 붙이고, 화면 문구에는 안 쓴다.
        executions: list[ToolExecution] = []
        # 스트리밍으로 고객에게 이미 보낸 텍스트. 저장하는 답변은 화면에 보인 것과 같아야 하므로 이것을 쓴다.
        shown: list[str] = []

        # llm_round는 1부터 6까지 번호다. 아래 _llm_round와 이름만 비슷하고, 하는 일이 다르다.
        for llm_round in range(1, self._max_llm_rounds + 1):
            model_turn: ModelTurn | None = None
            # 이 라운드에서 글자 조각을 이미 냈는지. 다음 라운드 글자 앞에 빈 줄을 한 번만 넣으려고 쓴다.
            round_started_text = False
            async with aclosing(self._llm_round(turns, definitions)) as items:
                async for item in items:
                    match item:
                        # TextDelta는 고객에게 보낼 응답
                        case TextDelta():
                            if shown and not round_started_text:
                                shown.append(_ROUND_SEPARATOR)
                                yield TextDelta(text=_ROUND_SEPARATOR)
                            round_started_text = True
                            shown.append(item.text)
                            yield item
                        # 이번 Gemini 호출의 마지막. 도구가 있을 수도, 글자만 있을 수도 있다. 실행은 아래 if에서 가른다.
                        case ModelTurn():
                            model_turn = item
            if model_turn is None:
                raise AgentEmptyResponseError("AI가 답변을 만들지 못했습니다. 다시 시도해 주세요.")

            # Function Call이 담긴 모델 응답도 그대로 Context에 남겨야 다음 요청에서 결과와 짝이 맞는다.
            turns.append(model_turn)

            # tool_calls는 리스트다. 그중 하나라도 reject_not_inquiry면 나머지는 실행하지 않는다.
            if any(call.name == "reject_not_inquiry" for call in model_turn.tool_calls):
                # 다른 Tool과 같이 왔어도 실행하지 않는다. 티켓을 만들거나 답을 저장하지 않는다.
                logger.info("Rejected a message that is not a store inquiry: conversation=%s", context.conversation.id)
                raise NotStoreInquiryError("매장에 대한 문의만 답변할 수 있습니다.")

            # 정상 종료. 프로필만으로 답하거나, 도구를 쓴 뒤 마지막 라운드에서 글자만 올 때다.
            # 1라운드에 도구 없이 글자만 와도 코드는 그 글자를 답으로 받는다.
            if not model_turn.tool_calls:
                if model_turn.text and model_turn.text.strip():
                    logger.info(
                        "Agent finished: conversation=%s llm_rounds=%d tools=%s",
                        context.conversation.id,
                        llm_round,
                        [execution.name for execution in executions],
                    )
                    # 화면으로 나간 조각이 있으면 그것을 잇는다. 조각이 없으면 ModelTurn에만 있는 글자를 쓴다.
                    content = "".join(shown).strip() if shown else model_turn.text.strip()
                    yield AgentReply(content=content, tool_executions=executions)
                    return
                logger.error(
                    "LLM returned neither a tool call nor text: conversation=%s finish_reason=%s",
                    context.conversation.id,
                    model_turn.finish_reason,
                )
                # 도구도 없고, 공백을 빼면 글자도 없다.
                raise AgentEmptyResponseError("AI가 답변을 만들지 못했습니다. 다시 시도해 주세요.")

            # 이미 실행한 개수와 이번에 요청한 개수의 합이 6을 넘으면 실행하지 않는다.
            if len(executions) + len(model_turn.tool_calls) > self._max_tool_calls:
                logger.error(
                    "Tool call limit (%d) exceeded: conversation=%s tools=%s",
                    self._max_tool_calls,
                    context.conversation.id,
                    [execution.name for execution in executions] + [call.name for call in model_turn.tool_calls],
                )
                raise AgentLimitExceededError("AI가 답변을 정리하지 못했습니다. 다시 시도해 주세요.")

            # 한 응답에 도구가 여러 개일 수 있다. 프롬프트는 조회 다음 검색이지만, 모델이 한 번에 넣으면 이 순서대로 실행한다.
            # ToolResult를 턴에 하나씩 붙이지 않는다. 결과 리스트 전체를 ToolResultTurn 하나에 넣는다.
            results = [await self._dispatcher.dispatch(context, call) for call in model_turn.tool_calls]
            executions += [ToolExecution(name=result.name, is_error=result.is_error) for result in results]
            turns.append(ToolResultTurn(results=results))

        logger.error(
            "LLM round limit (%d) exceeded: conversation=%s", self._max_llm_rounds, context.conversation.id
        )
        raise AgentLimitExceededError("AI가 답변을 정리하지 못했습니다. 다시 시도해 주세요.")

    def _llm_round(self, turns: list[Turn], definitions: Sequence[ToolDefinition]) -> AsyncGenerator[StreamItem]:
        """Gemini 스트림 객체를 만들어 돌려준다. """
        return self._provider.generate_stream(system_instruction=SYSTEM_INSTRUCTION, turns=turns, tools=definitions)


def _to_turn(message: Message) -> Turn:
    # role은 그 줄의 작성자다. user는 손님 글, 그 외(AI 답)는 글자만 있는 ModelTurn이다.
    if message.role is MessageRole.USER:
        return UserTurn(text=message.content)
    return ModelTurn(text=message.content)

import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError
from app.llm.types import JsonObject, ToolCall, ToolDefinition, ToolResult
from app.models import Conversation, EscalationReason, TicketStatus
from app.schemas import ErrorBody, ErrorDetail, ErrorResponse, KnowledgeSearchItem, StoreProfile
from app.services import knowledge_search_service, store_profile_service, support_ticket_service

logger = logging.getLogger(__name__)

type ToolStatus = Literal["ok", "unknown_tool", "invalid_arguments", "failed"]


@dataclass(frozen=True)
class AgentContext:
    """Backend가 채우는 실행 문맥. Tool 인자에는 없는 값이라 LLM이 바꿀 수 없다."""

    session: AsyncSession
    conversation: Conversation
    # 이번 요청의 고객 메시지. 티켓을 이 메시지에 연결한다.
    source_message_id: uuid.UUID

    @property
    def store_id(self) -> uuid.UUID:
        return self.conversation.store_id


# 모든 인자 모델은 extra="forbid"다. LLM이 store_id 같은 값을 끼워 넣으면 INVALID_ARGUMENTS가 된다.
class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchKnowledgeArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)] = Field(
        description='2-5 English keywords for what the customer wants to know, e.g. "dogs inside".'
    )


class CreateSupportTicketArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)] = Field(
        description="The customer's question rewritten as a clear, standalone question for the store owner."
    )
    # EscalationReason의 값과 같아야 한다(테스트가 확인한다). enum을 쓰면 JSON Schema에 $ref가 생겨서 Literal로 둔다.
    escalation_reason: Literal["no_knowledge", "insufficient_knowledge"] = Field(
        description=(
            '"no_knowledge" if the knowledge search returned no results, '
            '"insufficient_knowledge" if it returned results that do not answer the question.'
        )
    )


class KnowledgeSearchOutput(BaseModel):
    results: list[KnowledgeSearchItem]


class TicketCreationOutput(BaseModel):
    ticket_id: uuid.UUID
    status: TicketStatus
    # False면 이 고객 메시지로 이미 만든 티켓이다.
    created: bool


class AgentTool(Protocol):
    @property
    def name(self) -> str: ...

    def definition(self) -> ToolDefinition: ...

    async def run(self, context: AgentContext, arguments: JsonObject) -> BaseModel: ...


@dataclass(frozen=True)
class Tool[A: BaseModel]:
    """이름, 설명, 인자 모델, 실행 함수를 묶는다. 인자 모델이 Function Declaration의 스키마가 된다."""

    name: str
    description: str
    arguments_model: type[A]
    handler: Callable[[AgentContext, A], Awaitable[BaseModel]]

    def definition(self) -> ToolDefinition:
        schema = self.arguments_model.model_json_schema()
        return ToolDefinition(
            name=self.name, description=self.description, parameters=schema if schema.get("properties") else None
        )

    async def run(self, context: AgentContext, arguments: JsonObject) -> BaseModel:
        return await self.handler(context, self.arguments_model.model_validate(arguments))


async def _get_store_profile(context: AgentContext, _: NoArguments) -> StoreProfile:
    return await store_profile_service.get_store_profile(context.session, context.store_id)


async def _search_knowledge(context: AgentContext, arguments: SearchKnowledgeArguments) -> KnowledgeSearchOutput:
    results = await knowledge_search_service.search_knowledge(context.session, context.store_id, arguments.query)
    return KnowledgeSearchOutput(results=results)


async def _create_support_ticket(
    context: AgentContext, arguments: CreateSupportTicketArguments
) -> TicketCreationOutput:
    creation = await support_ticket_service.create_ticket(
        context.session,
        context.conversation,
        context.source_message_id,
        arguments.question,
        EscalationReason(arguments.escalation_reason),
    )
    return TicketCreationOutput(ticket_id=creation.ticket.id, status=creation.ticket.status, created=creation.created)


DEFAULT_TOOLS: tuple[AgentTool, ...] = (
    Tool(
        name="get_store_profile",
        description=(
            "Get the current store's basic information (name, description, address, phone, email, website, "
            "time zone) and its business hours for each day of the week."
        ),
        arguments_model=NoArguments,
        handler=_get_store_profile,
    ),
    Tool(
        name="search_knowledge",
        description=(
            "Search answers the store owner wrote for earlier customer questions (for example parking, pets, "
            "refunds). Returns the original question, the owner's answer and a relevance score for each match, "
            "or an empty list when nothing matches."
        ),
        arguments_model=SearchKnowledgeArguments,
        handler=_search_knowledge,
    ),
    Tool(
        name="create_support_ticket",
        description=(
            "Forward a customer question that cannot be answered from the store profile or the knowledge search "
            "to the store owner. Returns the ticket id and status."
        ),
        arguments_model=CreateSupportTicketArguments,
        handler=_create_support_ticket,
    ),
)


class ToolDispatcher:
    """LLM이 요청한 Tool 이름을 등록된 Python 함수로 연결하고, 결과를 Observation으로 만든다.

    실패해도 예외를 던지지 않고 오류 Observation을 돌려준다. LLM이 인자를 고치거나 티켓으로 넘기는 등
    다음 행동을 스스로 정하게 하기 위해서다. 단 프로그래밍 오류(그 외 예외)는 숨기지 않고 그대로 올린다.
    """

    def __init__(self, tools: Sequence[AgentTool] = DEFAULT_TOOLS) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def definitions(self) -> list[ToolDefinition]:
        return [tool.definition() for tool in self._tools.values()]

    async def dispatch(self, context: AgentContext, call: ToolCall) -> ToolResult:
        started = time.perf_counter()
        content, status = await self._execute(context, call)
        # 인자 값에는 고객이 쓴 문장이 들어갈 수 있으므로 로그에는 인자 이름만 남긴다.
        logger.info(
            "Tool %s finished: status=%s duration_ms=%.0f argument_keys=%s conversation=%s",
            call.name[:64],
            status,
            (time.perf_counter() - started) * 1000,
            sorted(call.arguments),
            context.conversation.id,
        )
        return ToolResult(call_id=call.id, name=call.name, content=content, is_error=status != "ok")

    async def _execute(self, context: AgentContext, call: ToolCall) -> tuple[JsonObject, ToolStatus]:
        tool = self._tools.get(call.name)
        if tool is None:
            message = f"Tool '{call.name}' does not exist. Available tools: {', '.join(self._tools)}."
            return _error("UNKNOWN_TOOL", message), "unknown_tool"
        try:
            # Tool마다 savepoint를 둔다. DB 오류가 나도 이 Tool의 변경만 취소되고 세션은 계속 쓸 수 있다.
            async with context.session.begin_nested():
                output = await tool.run(context, call.arguments)
        except ValidationError as exc:
            details = [
                ErrorDetail(loc=list(error["loc"]), msg=error["msg"], type=error["type"])
                for error in exc.errors(include_url=False, include_input=False, include_context=False)
            ]
            return _error("INVALID_ARGUMENTS", "The tool arguments are invalid.", details), "invalid_arguments"
        except (SQLAlchemyError, AppError) as exc:
            # SQL 파라미터에 고객 문장이 들어 있을 수 있어 예외 메시지 대신 종류만 남긴다.
            logger.error("Tool %s failed: %s", call.name, type(exc).__name__)
            return _error("TOOL_FAILED", "The tool failed due to a temporary internal error."), "failed"
        return output.model_dump(mode="json"), "ok"


def _error(code: str, message: str, details: list[ErrorDetail] | None = None) -> JsonObject:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details))
    return body.model_dump(mode="json", exclude_none=True)

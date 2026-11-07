"""테스트 전용 LLM Provider. 실제 Gemini API를 호출하지 않는다.

미리 정해 둔 응답(ModelTurn 또는 예외)을 순서대로 돌려주고, 받은 요청을 그대로 기록한다.
Tool 실행 순서는 이 스크립트가 아니라 AgentService와 ToolDispatcher가 응답을 처리하면서 정해진다.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import JsonValue

from app.llm.types import ModelTurn, ToolCall, ToolDefinition, ToolResult, ToolResultTurn, Turn


@dataclass(frozen=True)
class RecordedRequest:
    system_instruction: str
    # 호출 시점의 복사본. AgentService가 이후에 turns를 늘려도 바뀌지 않는다.
    turns: tuple[Turn, ...]
    tools: tuple[ToolDefinition, ...]


@dataclass
class MockGeminiProvider:
    responses: list[ModelTurn | Exception]
    requests: list[RecordedRequest] = field(default_factory=list)
    closed: bool = False

    async def generate(
        self, *, system_instruction: str, turns: Sequence[Turn], tools: Sequence[ToolDefinition]
    ) -> ModelTurn:
        self.requests.append(RecordedRequest(system_instruction, tuple(turns), tuple(tools)))
        if not self.responses:
            raise AssertionError("MockGeminiProvider: 준비된 응답이 더 없습니다.")
        match self.responses.pop(0):
            case Exception() as error:
                raise error
            case ModelTurn() as turn:
                return turn

    async def aclose(self) -> None:
        self.closed = True


def function_call(name: str, call_id: str | None = None, **arguments: JsonValue) -> ModelTurn:
    """Gemini가 Tool 하나를 호출하겠다고 응답한 것과 같은 ModelTurn."""
    return ModelTurn(tool_calls=[ToolCall(id=call_id or f"call-{name}", name=name, arguments=arguments)])


def final_text(text: str) -> ModelTurn:
    return ModelTurn(text=text, finish_reason="STOP")


def observation(request: RecordedRequest) -> ToolResultTurn:
    """요청의 마지막 turn이 Tool 결과(Observation)이면 반환한다."""
    match request.turns[-1]:
        case ToolResultTurn() as turn:
            return turn
        case other:
            raise AssertionError(f"마지막 turn이 Observation이 아닙니다: {other!r}")


def call_result_pairs(turns: Sequence[Turn]) -> list[tuple[ToolCall, ToolResult]]:
    """Context 안의 (Function Call, 그 결과) 짝. 결과 수가 호출 수와 다르면 zip(strict=True)가 실패한다."""
    pairs: list[tuple[ToolCall, ToolResult]] = []
    pending: list[ToolCall] = []
    for turn in turns:
        match turn:
            case ModelTurn(tool_calls=calls):
                pending = list(calls)
            case ToolResultTurn(results=results):
                pairs += zip(pending, results, strict=True)
                pending = []
            case _:
                pass
    return pairs


def turn_kinds(turns: Sequence[Turn]) -> list[str]:
    kinds: list[str] = []
    for turn in turns:
        match turn:
            case ToolResultTurn():
                kinds.append("observation")
            case ModelTurn(tool_calls=calls) if calls:
                kinds.append("model:" + ",".join(call.name for call in calls))
            case ModelTurn():
                kinds.append("model:text")
            case _:
                kinds.append("user")
    return kinds

"""테스트 전용 LLM Provider. 실제 Gemini API를 호출하지 않는다.

미리 정해 둔 응답(ModelTurn 또는 예외)을 순서대로 돌려주고, 받은 요청을 그대로 기록한다.
Tool 실행 순서는 이 스크립트가 아니라 AgentService와 ToolDispatcher가 응답을 처리하면서 정해진다.
"""

import asyncio
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass, field

from pydantic import JsonValue

from app.llm.types import (
    ModelTurn,
    StreamItem,
    TextDelta,
    ToolCall,
    ToolDefinition,
    ToolResult,
    ToolResultTurn,
    Turn,
)


@dataclass(frozen=True)
class RecordedRequest:
    system_instruction: str
    # 호출 시점의 복사본. AgentService가 이후에 turns를 늘려도 바뀌지 않는다.
    turns: tuple[Turn, ...]
    tools: tuple[ToolDefinition, ...]


@dataclass
class Chunks:
    """스트리밍 응답 한 번을 조각 단위로 적은 스크립트. generate_stream에서만 쓴다.

    steps의 문자열은 모델이 생성한 텍스트 조각, Exception은 그 지점에서 발생하는 오류,
    asyncio.Event는 테스트가 set할 때까지 스트림을 멈춘다(생성 도중인 상태를 만든다).
    """

    steps: list[str | Exception | asyncio.Event]
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class MockGeminiProvider:
    """responses를 요청마다 하나씩 소비한다.

    ModelTurn은 텍스트를 조각 하나로 내보내고, 여러 조각이 필요하면 Chunks를 쓴다.
    생성자는 Sequence를 받는다. list[ModelTurn]을 list[합집합]에 넣으면 원소를 더 넣을 수 있어서
    타입 검사가 거절하기 때문이다. 받은 값은 소비할 수 있게 리스트로 복사한다.
    """

    responses: list[ModelTurn | Exception | Chunks] = field(init=False)
    requests: list[RecordedRequest] = field(default_factory=list)
    closed: bool = False
    # 스트림이 끝까지 가지 못하고 닫히거나 취소된 횟수(연결 종료 테스트용)
    interrupted_streams: int = 0

    def __init__(self, responses: Sequence[ModelTurn | Exception | Chunks]) -> None:
        self.responses = list(responses)
        self.requests = []
        self.closed = False
        self.interrupted_streams = 0

    async def generate_stream(
        self, *, system_instruction: str, turns: Sequence[Turn], tools: Sequence[ToolDefinition]
    ) -> AsyncGenerator[StreamItem]:
        response = self._next(system_instruction, turns, tools)
        finished = False
        try:
            match response:
                case Exception() as error:
                    raise error
                case ModelTurn(text=text) as turn:
                    if text:
                        yield TextDelta(text=text)
                    yield turn
                case Chunks(steps=steps, tool_calls=tool_calls):
                    texts: list[str] = []
                    for step in steps:
                        match step:
                            case str():
                                texts.append(step)
                                yield TextDelta(text=step)
                            case Exception():
                                raise step
                            case asyncio.Event():
                                await step.wait()
                    yield ModelTurn(text="".join(texts) or None, tool_calls=tool_calls, finish_reason="STOP")
            finished = True
        finally:
            if not finished:
                self.interrupted_streams += 1

    async def aclose(self) -> None:
        self.closed = True

    def _next(
        self, system_instruction: str, turns: Sequence[Turn], tools: Sequence[ToolDefinition]
    ) -> ModelTurn | Exception | Chunks:
        self.requests.append(RecordedRequest(system_instruction, tuple(turns), tuple(tools)))
        if not self.responses:
            raise AssertionError("MockGeminiProvider: 준비된 응답이 더 없습니다.")
        return self.responses.pop(0)


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

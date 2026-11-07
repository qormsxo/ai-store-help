from collections.abc import AsyncGenerator, Sequence
from typing import Protocol

from app.llm.types import ModelTurn, StreamItem, ToolDefinition, Turn


class LLMProvider(Protocol):
    """LLM 호출 한 번을 담당한다. Tool 실행과 반복은 AgentService가 한다.

    실패는 app.errors의 LLM* 예외로 바꿔서 던진다(SDK 예외를 밖으로 내보내지 않는다).
    """

    async def generate(
        self, *, system_instruction: str, turns: Sequence[Turn], tools: Sequence[ToolDefinition]
    ) -> ModelTurn: ...

    def generate_stream(
        self, *, system_instruction: str, turns: Sequence[Turn], tools: Sequence[ToolDefinition]
    ) -> AsyncGenerator[StreamItem]:
        """generate와 같은 호출을 스트리밍으로 한다.

        텍스트가 생성되는 대로 TextDelta를 내보내고, 마지막에 항상 완성된 ModelTurn을 한 번 내보낸다.
        Function Call은 TextDelta로 나오지 않고 마지막 ModelTurn의 tool_calls에만 담긴다.
        """
        ...

    async def aclose(self) -> None: ...

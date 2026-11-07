from collections.abc import Sequence
from typing import Protocol

from app.llm.types import ModelTurn, ToolDefinition, Turn


class LLMProvider(Protocol):
    """LLM 호출 한 번을 담당한다. Tool 실행과 반복은 AgentService가 한다.

    실패는 app.errors의 LLM* 예외로 바꿔서 던진다(SDK 예외를 밖으로 내보내지 않는다).
    """

    async def generate(
        self, *, system_instruction: str, turns: Sequence[Turn], tools: Sequence[ToolDefinition]
    ) -> ModelTurn: ...

    async def aclose(self) -> None: ...

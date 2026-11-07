"""개발 서버용 가짜 LLM. 정해진 문장을 스트리밍하고 Gemini는 호출하지 않는다.

도구 호출도 하지 않는다. 티켓이나 지식 검색이 필요하면 LLM_PROVIDER=gemini로 바꾼다.
"""

import asyncio
from collections.abc import AsyncGenerator, Sequence

from app.llm.types import ModelTurn, StreamItem, TextDelta, ToolDefinition, Turn, UserTurn

# 화면에서 조각이 이어지는 것이 보이도록 나눈다.
_PREFIX = ("개발용 모의 답변입니다. ", "실제 AI는 호출하지 않았습니다.")
_CHUNK_DELAY_SECONDS = 0.15


class DevMockProvider:
    async def generate(
        self, *, system_instruction: str, turns: Sequence[Turn], tools: Sequence[ToolDefinition]
    ) -> ModelTurn:
        del system_instruction, tools
        return ModelTurn(text="".join(_parts(turns)))

    async def generate_stream(
        self, *, system_instruction: str, turns: Sequence[Turn], tools: Sequence[ToolDefinition]
    ) -> AsyncGenerator[StreamItem]:
        del system_instruction, tools
        parts = _parts(turns)
        for part in parts:
            await asyncio.sleep(_CHUNK_DELAY_SECONDS)
            yield TextDelta(text=part)
        yield ModelTurn(text="".join(parts))

    async def aclose(self) -> None:
        return None


def _parts(turns: Sequence[Turn]) -> tuple[str, ...]:
    question = ""
    for turn in turns:
        match turn:
            case UserTurn(text=text):
                question = text
            case _:
                pass
    if question == "":
        return _PREFIX
    return (*_PREFIX, f" 질문: {question}")

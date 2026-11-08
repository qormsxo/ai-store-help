"""Gemini SDK 호출 함수의 모양. 구현은 SDK가 하고, 테스트는 가짜를 넣는다."""

from collections.abc import AsyncIterator
from typing import Protocol, TypeGuard, runtime_checkable

from google.genai.types import Content, GenerateContentConfig, GenerateContentResponse


class GenerateContentStream(Protocol):
    """스트리밍 SDK 함수 `client.aio.models.generate_content_stream`의 모양.

    SDK 선언은 AsyncIterator다. 실제 객체는 aclose()가 있는 async generator인 경우가 많아서,
    받은 뒤 닫을 수 있으면 닫는다.
    """

    async def __call__(
        self, *, model: str, contents: list[Content], config: GenerateContentConfig
    ) -> AsyncIterator[GenerateContentResponse]: ...


@runtime_checkable
class _ClosableStream(Protocol):
    async def aclose(self) -> None: ...


def is_closable(stream: AsyncIterator[GenerateContentResponse]) -> TypeGuard[_ClosableStream]:
    return isinstance(stream, _ClosableStream)

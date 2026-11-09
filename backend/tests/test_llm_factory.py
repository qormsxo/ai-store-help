import pytest
from pydantic import SecretStr

from app.config import Settings
from app.errors import LLMNotConfiguredError
from app.llm.factory import create_llm_provider
from app.llm.types import ModelTurn, TextDelta, UserTurn


def settings_with(api_key: str | None, model: str | None = "gemini-test-model") -> Settings:
    return Settings(
        llm_provider="gemini",
        gemini_api_key=SecretStr(api_key) if api_key is not None else None,
        gemini_model=model,
    )


async def test_missing_api_key_is_configuration_error() -> None:
    with pytest.raises(LLMNotConfiguredError) as exc_info:
        create_llm_provider(settings_with(None))

    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "LLM_NOT_CONFIGURED"
    assert "GEMINI_API_KEY" in exc_info.value.message


async def test_mock_provider_streams_a_fixed_answer_without_network() -> None:
    provider = create_llm_provider(Settings(llm_provider="mock"))

    items = [
        item
        async for item in provider.generate_stream(
            system_instruction="system", turns=[UserTurn(text="영업시간?")], tools=[]
        )
    ]
    await provider.aclose()

    text = "개발용 모의 답변입니다. 실제 AI는 호출하지 않았습니다. 질문: 영업시간?"
    assert items == [
        TextDelta(text="개발용 모의 답변입니다. "),
        TextDelta(text="실제 AI는 호출하지 않았습니다."),
        TextDelta(text=" 질문: 영업시간?"),
        ModelTurn(text=text),
    ]

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.errors import LLMNotConfiguredError
from app.llm.factory import create_llm_provider


def settings_with(api_key: str | None, model: str | None = "gemini-test-model") -> Settings:
    return Settings(gemini_api_key=SecretStr(api_key) if api_key is not None else None, gemini_model=model)


@pytest.mark.parametrize("api_key", [None, "", "   "])
async def test_missing_api_key_is_configuration_error(api_key: str | None) -> None:
    with pytest.raises(LLMNotConfiguredError) as exc_info:
        create_llm_provider(settings_with(api_key))

    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "LLM_NOT_CONFIGURED"
    assert "GEMINI_API_KEY" in exc_info.value.message


@pytest.mark.parametrize("model", [None, ""])
async def test_missing_model_is_configuration_error(model: str | None) -> None:
    with pytest.raises(LLMNotConfiguredError) as exc_info:
        create_llm_provider(settings_with("test-key-not-real", model))

    assert "GEMINI_MODEL" in exc_info.value.message


async def test_provider_is_created_and_closed_without_network() -> None:
    provider = create_llm_provider(settings_with("test-key-not-real"))

    await provider.aclose()


async def test_api_key_is_not_exposed_in_settings_repr() -> None:
    settings = settings_with("test-key-not-real")

    assert "test-key-not-real" not in repr(settings)

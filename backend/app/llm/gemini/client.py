"""환경변수로 Gemini SDK 클라이언트를 만든다."""

from google.genai import Client
from google.genai.types import HttpOptions

from app.config import Settings
from app.errors import LLMNotConfiguredError
from app.llm.gemini.provider import GeminiProvider


def create_gemini_provider(settings: Settings) -> GeminiProvider:
    """환경변수로 SDK 클라이언트를 만들고 GeminiProvider를 초기화한다. 키나 모델이 없으면 LLMNotConfiguredError."""
    api_key = settings.gemini_api_key.get_secret_value().strip() if settings.gemini_api_key else ""
    if not api_key:
        raise LLMNotConfiguredError("GEMINI_API_KEY가 설정되지 않았습니다.")
    model = (settings.gemini_model or "").strip()
    if not model:
        raise LLMNotConfiguredError("GEMINI_MODEL이 설정되지 않았습니다.")

    client = Client(
        api_key=api_key,
        # SDK 자체 재시도는 쓰지 않는다(기본값도 재시도 없음). 재시도는 GeminiProvider가 정한 규칙으로만 한다.
        http_options=HttpOptions(timeout=int(settings.gemini_timeout * 1000)),
    )
    return GeminiProvider(
        client.aio.models.generate_content,
        generate_content_stream=client.aio.models.generate_content_stream,
        model=model,
        max_output_tokens=settings.gemini_max_output_tokens,
        timeout_seconds=settings.gemini_timeout,
        close=client.aio.aclose,
    )

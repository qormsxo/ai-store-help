import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# 로컬 실행 시 저장소 루트의 .env를 읽는다. Docker에서는 compose가 환경변수를 주입하므로 파일이 없어도 된다.
_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ROOT_ENV, extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://app:app@localhost:5432/app"
    # 이 실행 환경이 응대하는 매장. 요청이나 LLM이 아니라 서버 설정으로만 정해진다.
    default_store_slug: str = "oak-pine-demo"

    # mock이면 Gemini를 호출하지 않고 정해진 문장을 스트리밍한다. 실제 모델은 gemini.
    llm_provider: Literal["gemini", "mock"] = "gemini"
    # Gemini. 키와 모델은 코드에 기본값을 두지 않는다. 없으면 Agent 호출 시 LLM_NOT_CONFIGURED 오류가 난다.
    gemini_api_key: SecretStr | None = None
    gemini_model: str | None = None
    gemini_max_output_tokens: int = Field(default=2048, gt=0)
    # 요청 한 번(시도 한 번)의 제한 시간, 초 단위.
    gemini_timeout: float = Field(default=60, gt=0)

    @field_validator("default_store_slug")
    @classmethod
    def _validate_store_slug(cls, value: str) -> str:
        if not SLUG_PATTERN.fullmatch(value):
            raise ValueError("DEFAULT_STORE_SLUG는 소문자, 숫자, 하이픈만 쓸 수 있습니다 (예: oak-pine-demo)")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()

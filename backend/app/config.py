from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 로컬 실행 시 저장소 루트의 .env를 읽는다. Docker에서는 compose가 환경변수를 주입하므로 파일이 없어도 된다.
_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ROOT_ENV, extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://app:app@localhost:5432/app"


@lru_cache
def get_settings() -> Settings:
    return Settings()

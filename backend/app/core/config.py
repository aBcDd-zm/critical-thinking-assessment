from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    APP_NAME: str = "Critical Thinking Assessment API"
    APP_ENV: str = "local"
    API_V1_PREFIX: str = "/api/v1"
    DATABASE_URL: str = (
        "mysql+pymysql://root:password@127.0.0.1:3306/"
        "psychological_assessment?charset=utf8mb4"
    )
    SQL_ECHO: bool = False
    BACKEND_CORS_ORIGINS: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    MODEL_PROVIDER: str = "deepseek"
    MODEL_GATEWAY_MODE: str = "mock"
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-v4-pro"
    DEEPSEEK_TIMEOUT_SECONDS: int = 60
    DEEPSEEK_ENABLE_THINKING: bool = True
    DEEPSEEK_REASONING_EFFORT: str = "high"
    INTERVIEW_FLOW_VERSION: str = "progressive_v3_3"
    CONSULTATIVE_TURN_TIMEOUT_SECONDS: int = 10
    INTERVIEWER_STYLE_ENABLED: bool = False
    INTERVIEWER_STYLE_DEFAULT: str = "baseline_v1"
    INTERVIEWER_RENDER_TIMEOUT_SECONDS: int = Field(default=3, ge=1, le=3)
    CANDIDATE_GENERATION_TIMEOUT_SECONDS: int = Field(
        default=15,
        ge=1,
        le=60,
    )
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin123456"
    ADMIN_DISPLAY_NAME: str = "系统管理员"
    ADMIN_TOKEN_SECRET: str = "change-me-in-production"
    EXPORT_PSEUDONYM_SECRET: str = "change-this-export-pseudonym-secret"
    ADMIN_TOKEN_EXPIRE_MINUTES: int = 1440
    ADMIN_BOOTSTRAP_ENABLED: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()

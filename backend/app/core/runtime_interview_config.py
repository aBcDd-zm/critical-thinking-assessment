from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeInterviewSettings(BaseSettings):
    """Mutable live-interview budgets kept outside frozen generation config."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    RUNTIME_CONSULTATIVE_TURN_TIMEOUT_SECONDS: int = Field(
        default=8,
        ge=5,
        le=15,
    )
    RUNTIME_INTERVIEWER_RENDER_TIMEOUT_SECONDS: int = Field(
        default=5,
        ge=1,
        le=6,
    )
    RUNTIME_HUMANISTIC_V11_MODEL_POLISH_MODE: Literal[
        "off",
        "complex_only",
        "adaptive",
        "always",
    ] = "adaptive"


@lru_cache
def get_runtime_interview_settings() -> RuntimeInterviewSettings:
    return RuntimeInterviewSettings()

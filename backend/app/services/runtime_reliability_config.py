from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeReliabilitySettings(BaseSettings):
    """Online-only controls kept separate from the frozen generation config."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    HUMANISTIC_RUNTIME_RENDER_TIMEOUT_SECONDS: float | None = Field(
        default=None,
        ge=1,
        le=15,
    )
    SCORING_REPORT_TIMEOUT_SECONDS: float = Field(default=30, ge=5, le=60)


@lru_cache
def get_runtime_reliability_settings() -> RuntimeReliabilitySettings:
    return RuntimeReliabilitySettings()


def humanistic_renderer_timeout_seconds(settings: Any) -> float:
    """Return the online-only renderer deadline without changing frozen assets."""
    configured = (
        get_runtime_reliability_settings()
        .HUMANISTIC_RUNTIME_RENDER_TIMEOUT_SECONDS
    )
    return (
        configured
        if configured is not None
        else float(settings.INTERVIEWER_RENDER_TIMEOUT_SECONDS)
    )


def scoring_report_timeout_seconds() -> float:
    return get_runtime_reliability_settings().SCORING_REPORT_TIMEOUT_SECONDS


__all__ = [
    "humanistic_renderer_timeout_seconds",
    "get_runtime_reliability_settings",
    "scoring_report_timeout_seconds",
]

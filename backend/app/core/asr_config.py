"""Isolated speech-to-text settings for the optional voice input channel.

The assessment measurement configuration is intentionally not imported or
modified here.  ASR is a transport convenience: uploaded audio is processed in
memory, returned as editable text, and is never persisted by this module.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ASRSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    ASR_MODE: Literal["disabled", "doubao"] = "disabled"
    DOUBAO_ASR_API_KEY: str = ""
    # A deployment may deliberately use the same new-console API Key for TTS
    # and ASR.  The dedicated ASR value always wins when both are present.
    DOUBAO_TTS_API_KEY: str = ""
    DOUBAO_ASR_TIMEOUT_SECONDS: float = Field(default=25, gt=0, le=60)
    DOUBAO_ASR_MAX_ATTEMPTS: int = Field(default=2, ge=1, le=2)
    DOUBAO_ASR_RETRY_DELAY_SECONDS: float = Field(default=0.25, ge=0, le=2)
    DOUBAO_ASR_CONVERSION_TIMEOUT_SECONDS: float = Field(default=12, gt=0, le=30)

    @property
    def effective_api_key(self) -> str:
        return (self.DOUBAO_ASR_API_KEY or self.DOUBAO_TTS_API_KEY).strip()


@lru_cache(maxsize=1)
def get_asr_settings() -> ASRSettings:
    return ASRSettings()


__all__ = ["ASRSettings", "get_asr_settings"]

"""Isolated TTS settings that do not alter frozen assessment configuration.

The humanistic evaluation bundle hashes ``app/core/config.py``.  Speech is an
optional delivery channel, so its operational settings live separately and
cannot silently invalidate or re-bless that frozen measurement artifact.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TTSSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    TTS_MODE: Literal["disabled", "doubao"] = "disabled"
    DOUBAO_TTS_API_KEY: str = ""
    DOUBAO_TTS_RESOURCE_ID: str = "seed-tts-2.0"
    DOUBAO_TTS_SPEAKER: str = "zh_male_ruyayichen_saturn_bigtts"
    DOUBAO_TTS_SAMPLE_RATE: int = Field(default=24_000, ge=24_000, le=24_000)
    DOUBAO_TTS_BIT_RATE: int = Field(default=128_000, ge=128_000, le=128_000)
    DOUBAO_TTS_SPEECH_RATE: int = Field(default=-5, ge=-50, le=100)
    DOUBAO_TTS_CONTEXT_TEXT: str = Field(
        default=(
            "请像一位成熟、温和、专注的访谈者面对面自然交谈。语气真诚克制，不要播音腔，"
            "不要教学腔，不要逐字重读；停顿自然，句尾轻收。"
        ),
        max_length=500,
    )
    DOUBAO_TTS_TIMEOUT_SECONDS: float = Field(default=25, gt=0, le=60)
    DOUBAO_TTS_MAX_ATTEMPTS: int = Field(default=2, ge=1, le=2)
    DOUBAO_TTS_RETRY_DELAY_SECONDS: float = Field(default=0.25, ge=0, le=2)


@lru_cache(maxsize=1)
def get_tts_settings() -> TTSSettings:
    return TTSSettings()


__all__ = ["TTSSettings", "get_tts_settings"]

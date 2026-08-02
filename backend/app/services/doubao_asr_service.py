"""Privacy-bounded Doubao flash ASR adapter for browser-recorded audio.

Only opaque request metadata and a base64 representation of the in-memory
audio are sent to the fixed official endpoint.  Audio, transcripts, API keys,
session identifiers, and provider error bodies are never logged or persisted.
"""

from __future__ import annotations

import asyncio
import base64
import math
import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Awaitable, Callable, Literal
from uuid import uuid4

import httpx

from app.core.asr_config import ASRSettings, get_asr_settings


DOUBAO_FLASH_ASR_URL = (
    "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"
)
DOUBAO_FLASH_ASR_RESOURCE_ID = "volc.bigasr.auc_turbo"
DOUBAO_FLASH_ASR_SUCCESS_CODE = "20000000"
DOUBAO_FLASH_ASR_BUSY_CODE = "55000031"

# Public endpoint guardrails.  The supplier permits much larger files, but the
# product records one short answer at a time and should not become a generic
# transcription proxy.
MAX_ASR_UPLOAD_BYTES = 5 * 1024 * 1024
ASR_RATE_LIMIT_PER_MINUTE = 6
ASR_RATE_LIMIT_WINDOW_SECONDS = 60
MAX_ASR_RECORDING_SECONDS = 60
MAX_TRANSCODED_AUDIO_BYTES = 4 * 1024 * 1024

_NATIVE_MIME_TYPES = frozenset(
    {
        "audio/wav",
        "audio/x-wav",
        "audio/wave",
        "audio/mpeg",
        "audio/mp3",
        "audio/ogg",
        "application/ogg",
    }
)
_TRANSCODE_MIME_TYPES = frozenset(
    {
        "audio/webm",
        "video/webm",
        "audio/mp4",
        "audio/x-m4a",
        "audio/aac",
    }
)
SUPPORTED_ASR_MIME_TYPES = _NATIVE_MIME_TYPES | _TRANSCODE_MIME_TYPES

ASRErrorReason = Literal[
    "asr_disabled",
    "credentials_missing",
    "empty_audio",
    "audio_too_large",
    "unsupported_audio_type",
    "converter_unavailable",
    "conversion_timeout",
    "conversion_failed",
    "provider_timeout",
    "provider_network_error",
    "provider_rejected_request",
    "provider_protocol_error",
    "no_speech_detected",
]

HTTPPost = Callable[..., Awaitable[httpx.Response]]
ProcessFactory = Callable[..., Awaitable[asyncio.subprocess.Process]]


def normalize_audio_content_type(content_type: str | None) -> str:
    return str(content_type or "").split(";", 1)[0].strip().lower()


def is_supported_audio_content_type(content_type: str | None) -> bool:
    return normalize_audio_content_type(content_type) in SUPPORTED_ASR_MIME_TYPES


@dataclass(frozen=True)
class ASRConfig:
    mode: Literal["disabled", "doubao"] = "disabled"
    api_key: str = field(default="", repr=False)
    timeout_seconds: float = 25
    max_attempts: int = 2
    retry_delay_seconds: float = 0.25
    conversion_timeout_seconds: float = 12

    def __post_init__(self) -> None:
        if self.mode not in {"disabled", "doubao"}:
            raise ValueError("ASR mode must be doubao or disabled")
        if not 0 < self.timeout_seconds <= 60:
            raise ValueError("ASR timeout must be between zero and 60 seconds")
        if self.max_attempts not in {1, 2}:
            raise ValueError("ASR max_attempts must be one or two")
        if not 0 <= self.retry_delay_seconds <= 2:
            raise ValueError("ASR retry delay must be between zero and two seconds")
        if not 0 < self.conversion_timeout_seconds <= 30:
            raise ValueError("ASR conversion timeout must be positive")

    @classmethod
    def from_app_settings(cls, settings: ASRSettings | None = None) -> "ASRConfig":
        app_settings = settings or get_asr_settings()
        return cls(
            mode=app_settings.ASR_MODE,
            api_key=app_settings.effective_api_key,
            timeout_seconds=app_settings.DOUBAO_ASR_TIMEOUT_SECONDS,
            max_attempts=app_settings.DOUBAO_ASR_MAX_ATTEMPTS,
            retry_delay_seconds=app_settings.DOUBAO_ASR_RETRY_DELAY_SECONDS,
            conversion_timeout_seconds=(
                app_settings.DOUBAO_ASR_CONVERSION_TIMEOUT_SECONDS
            ),
        )


@dataclass(frozen=True)
class ASRTranscriptionResult:
    request_id: str
    provider: str = "doubao"
    text: str | None = field(default=None, repr=False)
    error_reason: ASRErrorReason | None = None

    @property
    def ok(self) -> bool:
        return self.error_reason is None and self.text is not None


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0


class SessionASRRateLimiter:
    """Small in-memory abuse guard; no audio or transcript enters the bucket."""

    def __init__(
        self,
        *,
        limit: int = ASR_RATE_LIMIT_PER_MINUTE,
        window_seconds: float = ASR_RATE_LIMIT_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit <= 0 or window_seconds <= 0:
            raise ValueError("ASR rate limit must be positive")
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._buckets: dict[str, deque[float]] = {}
        self._lock = Lock()

    def acquire(self, session_uuid: str) -> RateLimitDecision:
        now = self._clock()
        cutoff = now - self.window_seconds
        with self._lock:
            bucket = self._buckets.setdefault(session_uuid, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                retry_after = max(1, math.ceil(bucket[0] + self.window_seconds - now))
                return RateLimitDecision(False, retry_after)
            bucket.append(now)
            return RateLimitDecision(True)

    def clear(self) -> None:
        with self._lock:
            self._buckets.clear()


_session_asr_rate_limiter = SessionASRRateLimiter()


def get_session_asr_rate_limiter() -> SessionASRRateLimiter:
    return _session_asr_rate_limiter


class DoubaoASRService:
    def __init__(
        self,
        config: ASRConfig | None = None,
        *,
        http_post: HTTPPost | None = None,
        process_factory: ProcessFactory = asyncio.create_subprocess_exec,
        request_id_factory: Callable[[], str] = lambda: str(uuid4()),
    ) -> None:
        self.config = config or ASRConfig.from_app_settings()
        self._http_post = http_post or self._post_with_httpx
        self._process_factory = process_factory
        self._request_id_factory = request_id_factory

    async def transcribe(
        self,
        audio: bytes,
        content_type: str,
        *,
        request_id: str | None = None,
    ) -> ASRTranscriptionResult:
        opaque_request_id = request_id or self._request_id_factory()
        if self.config.mode != "doubao":
            return self._error(opaque_request_id, "asr_disabled")
        if not self.config.api_key.strip():
            return self._error(opaque_request_id, "credentials_missing")
        if not audio:
            return self._error(opaque_request_id, "empty_audio")
        if len(audio) > MAX_ASR_UPLOAD_BYTES:
            return self._error(opaque_request_id, "audio_too_large")

        normalized_type = normalize_audio_content_type(content_type)
        if normalized_type not in SUPPORTED_ASR_MIME_TYPES:
            return self._error(opaque_request_id, "unsupported_audio_type")

        provider_audio = audio
        if normalized_type in _TRANSCODE_MIME_TYPES:
            provider_audio, conversion_error = await self._transcode_to_ogg(audio)
            if conversion_error is not None:
                return self._error(opaque_request_id, conversion_error)

        payload = {
            "user": {"uid": "assessment-web"},
            "audio": {"data": base64.b64encode(provider_audio).decode("ascii")},
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
            },
        }
        headers = {
            "X-Api-Key": self.config.api_key.strip(),
            "X-Api-Resource-Id": DOUBAO_FLASH_ASR_RESOURCE_ID,
            "X-Api-Request-Id": opaque_request_id,
            "X-Api-Sequence": "-1",
        }

        response: httpx.Response | None = None
        for attempt in range(self.config.max_attempts):
            try:
                response = await self._http_post(
                    DOUBAO_FLASH_ASR_URL,
                    json=payload,
                    headers=headers,
                    timeout=self.config.timeout_seconds,
                )
                if (
                    response.status_code >= 500
                    or response.headers.get("X-Api-Status-Code")
                    == DOUBAO_FLASH_ASR_BUSY_CODE
                ) and attempt + 1 < self.config.max_attempts:
                    if self.config.retry_delay_seconds:
                        await asyncio.sleep(self.config.retry_delay_seconds)
                    continue
                break
            except httpx.TimeoutException:
                if attempt + 1 >= self.config.max_attempts:
                    return self._error(opaque_request_id, "provider_timeout")
            except httpx.RequestError:
                if attempt + 1 >= self.config.max_attempts:
                    return self._error(opaque_request_id, "provider_network_error")
            if self.config.retry_delay_seconds:
                await asyncio.sleep(self.config.retry_delay_seconds)

        if response is None:  # Defensive; every loop exit above sets or returns.
            return self._error(opaque_request_id, "provider_network_error")

        if (
            response.status_code != 200
            or response.headers.get("X-Api-Status-Code")
            != DOUBAO_FLASH_ASR_SUCCESS_CODE
        ):
            return self._error(opaque_request_id, "provider_rejected_request")

        try:
            response_payload = response.json()
            text = response_payload["result"]["text"]
        except (KeyError, TypeError, ValueError):
            return self._error(opaque_request_id, "provider_protocol_error")
        if not isinstance(text, str):
            return self._error(opaque_request_id, "provider_protocol_error")
        cleaned_text = text.strip()
        if not cleaned_text:
            return self._error(opaque_request_id, "no_speech_detected")
        return ASRTranscriptionResult(
            request_id=opaque_request_id,
            provider="doubao",
            text=cleaned_text,
        )

    async def _post_with_httpx(self, url: str, **kwargs: object) -> httpx.Response:
        timeout = float(kwargs.pop("timeout", self.config.timeout_seconds))
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(url, **kwargs)

    async def _transcode_to_ogg(
        self,
        audio: bytes,
    ) -> tuple[bytes, ASRErrorReason | None]:
        try:
            process = await self._process_factory(
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                "pipe:0",
                "-t",
                str(MAX_ASR_RECORDING_SECONDS),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "libopus",
                "-b:a",
                "32k",
                "-application",
                "voip",
                "-f",
                "ogg",
                "pipe:1",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, OSError):
            return b"", "converter_unavailable"

        try:
            converted, _private_stderr = await asyncio.wait_for(
                process.communicate(audio),
                timeout=self.config.conversion_timeout_seconds,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return b"", "conversion_timeout"

        if (
            process.returncode != 0
            or not converted.startswith(b"OggS")
            or len(converted) > MAX_TRANSCODED_AUDIO_BYTES
        ):
            return b"", "conversion_failed"
        return converted, None

    @staticmethod
    def _error(
        request_id: str,
        reason: ASRErrorReason,
    ) -> ASRTranscriptionResult:
        return ASRTranscriptionResult(
            request_id=request_id,
            provider="doubao",
            error_reason=reason,
        )


def get_doubao_asr_service() -> DoubaoASRService:
    return DoubaoASRService()


__all__ = [
    "ASRConfig",
    "ASRTranscriptionResult",
    "ASR_RATE_LIMIT_PER_MINUTE",
    "DOUBAO_FLASH_ASR_BUSY_CODE",
    "DOUBAO_FLASH_ASR_RESOURCE_ID",
    "DOUBAO_FLASH_ASR_SUCCESS_CODE",
    "DOUBAO_FLASH_ASR_URL",
    "DoubaoASRService",
    "MAX_ASR_UPLOAD_BYTES",
    "SUPPORTED_ASR_MIME_TYPES",
    "SessionASRRateLimiter",
    "get_doubao_asr_service",
    "get_session_asr_rate_limiter",
    "is_supported_audio_content_type",
    "normalize_audio_content_type",
]

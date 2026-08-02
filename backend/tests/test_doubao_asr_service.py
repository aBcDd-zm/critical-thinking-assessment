from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from starlette.requests import Request

from app.core.asr_config import ASRSettings
from app.services.doubao_asr_service import (
    ASRConfig,
    ASRTranscriptionResult,
    DOUBAO_FLASH_ASR_RESOURCE_ID,
    DOUBAO_FLASH_ASR_URL,
    MAX_ASR_UPLOAD_BYTES,
    DoubaoASRService,
    SessionASRRateLimiter,
    is_supported_audio_content_type,
)


def doubao_config(**overrides: Any) -> ASRConfig:
    values: dict[str, Any] = {
        "mode": "doubao",
        "api_key": "private-api-key",
        "timeout_seconds": 1,
        "max_attempts": 2,
        "retry_delay_seconds": 0,
        "conversion_timeout_seconds": 1,
    }
    values.update(overrides)
    return ASRConfig(**values)


def raw_audio_request(
    body: bytes,
    content_type: str = "audio/wav",
    *,
    chunks: list[bytes] | None = None,
) -> Request:
    pending = list(chunks if chunks is not None else [body])

    async def receive() -> dict[str, Any]:
        if pending:
            chunk = pending.pop(0)
            return {
                "type": "http.request",
                "body": chunk,
                "more_body": bool(pending),
            }
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"content-type", content_type.encode("ascii"))],
        },
        receive,
    )


def test_asr_settings_use_dedicated_key_then_explicit_tts_fallback() -> None:
    dedicated = ASRSettings(
        _env_file=None,
        ASR_MODE="doubao",
        DOUBAO_ASR_API_KEY="asr-key",
        DOUBAO_TTS_API_KEY="tts-key",
    )
    shared = ASRSettings(
        _env_file=None,
        ASR_MODE="doubao",
        DOUBAO_ASR_API_KEY="",
        DOUBAO_TTS_API_KEY="tts-key",
    )

    assert dedicated.effective_api_key == "asr-key"
    assert shared.effective_api_key == "tts-key"
    assert "asr-key" not in repr(ASRConfig.from_app_settings(dedicated))


def test_supported_mobile_mime_types_ignore_codec_parameters() -> None:
    assert is_supported_audio_content_type("audio/webm;codecs=opus")
    assert is_supported_audio_content_type("audio/mp4; codecs=mp4a.40.2")
    assert is_supported_audio_content_type("audio/ogg")
    assert not is_supported_audio_content_type("application/octet-stream")


def test_flash_request_uses_fixed_official_contract_and_returns_text() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        calls.append((url, kwargs))
        return httpx.Response(
            200,
            headers={"X-Api-Status-Code": "20000000"},
            json={"result": {"text": "  这是转写文本。  "}},
        )

    result = asyncio.run(
        DoubaoASRService(
            doubao_config(),
            http_post=fake_post,
            request_id_factory=lambda: "request-asr-1",
        ).transcribe(b"RIFF-wave-audio", "audio/wav")
    )

    assert result.ok and result.text == "这是转写文本。"
    assert result.request_id == "request-asr-1"
    assert "这是转写文本" not in repr(result)
    assert calls[0][0] == DOUBAO_FLASH_ASR_URL
    headers = calls[0][1]["headers"]
    assert headers == {
        "X-Api-Key": "private-api-key",
        "X-Api-Resource-Id": DOUBAO_FLASH_ASR_RESOURCE_ID,
        "X-Api-Request-Id": "request-asr-1",
        "X-Api-Sequence": "-1",
    }
    payload = calls[0][1]["json"]
    assert payload["user"] == {"uid": "assessment-web"}
    assert payload["audio"] == {
        "data": base64.b64encode(b"RIFF-wave-audio").decode("ascii")
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "private-api-key" not in serialized
    assert "session_uuid" not in serialized


def test_webm_is_transcoded_to_ogg_through_pipes_without_a_temporary_file() -> None:
    process = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(return_value=(b"OggS-converted-opus", b"private stderr")),
        kill=MagicMock(),
        wait=AsyncMock(),
    )
    process_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def process_factory(*args: Any, **kwargs: Any) -> Any:
        process_calls.append((args, kwargs))
        return process

    async def fake_post(_url: str, **_kwargs: Any) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"X-Api-Status-Code": "20000000"},
            json={"result": {"text": "微信录音"}},
        )

    result = asyncio.run(
        DoubaoASRService(
            doubao_config(),
            http_post=fake_post,
            process_factory=process_factory,
        ).transcribe(b"webm-opus", "audio/webm;codecs=opus")
    )

    assert result.ok
    args, kwargs = process_calls[0]
    assert args[0] == "ffmpeg"
    assert "pipe:0" in args and "pipe:1" in args
    assert kwargs["stdin"] == asyncio.subprocess.PIPE
    assert process.communicate.await_args.args == (b"webm-opus",)
    assert process.kill.call_count == 0


def test_missing_converter_and_provider_details_fail_closed() -> None:
    async def missing_process(*_args: Any, **_kwargs: Any) -> Any:
        raise FileNotFoundError("private local path")

    conversion = asyncio.run(
        DoubaoASRService(
            doubao_config(),
            process_factory=missing_process,
        ).transcribe(b"webm", "audio/webm", request_id="convert-request")
    )

    async def rejected(_url: str, **_kwargs: Any) -> httpx.Response:
        return httpx.Response(
            403,
            headers={"X-Api-Status-Code": "45000000"},
            json={"message": "private provider message and transcript"},
        )

    provider = asyncio.run(
        DoubaoASRService(doubao_config(), http_post=rejected).transcribe(
            b"RIFF-audio", "audio/wav", request_id="provider-request"
        )
    )

    assert conversion.error_reason == "converter_unavailable"
    assert provider.error_reason == "provider_rejected_request"
    assert "private provider" not in repr(provider)
    assert "private-api-key" not in repr(provider)


def test_network_timeout_retries_once_and_returns_stable_reason() -> None:
    calls = 0

    async def timeout(_url: str, **_kwargs: Any) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("private timeout details")

    result = asyncio.run(
        DoubaoASRService(doubao_config(), http_post=timeout).transcribe(
            b"RIFF-audio", "audio/wav", request_id="timeout-request"
        )
    )

    assert calls == 2
    assert result.error_reason == "provider_timeout"
    assert result.request_id == "timeout-request"


@pytest.mark.parametrize(
    "first_response",
    [
        httpx.Response(
            503,
            headers={"X-Api-Status-Code": "55000000"},
            json={"message": "private temporary failure"},
        ),
        httpx.Response(
            200,
            headers={"X-Api-Status-Code": "55000031"},
            json={"message": "private provider busy detail"},
        ),
    ],
)
def test_transient_provider_response_gets_exactly_one_retry(
    first_response: httpx.Response,
) -> None:
    responses = [
        first_response,
        httpx.Response(
            200,
            headers={"X-Api-Status-Code": "20000000"},
            json={"result": {"text": "重试成功"}},
        ),
    ]
    calls = 0

    async def post(_url: str, **_kwargs: Any) -> httpx.Response:
        nonlocal calls
        response = responses[calls]
        calls += 1
        return response

    result = asyncio.run(
        DoubaoASRService(doubao_config(), http_post=post).transcribe(
            b"RIFF-audio", "audio/wav"
        )
    )

    assert result.ok and result.text == "重试成功"
    assert calls == 2


def test_authentication_rejection_is_not_retried() -> None:
    calls = 0

    async def rejected(_url: str, **_kwargs: Any) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            401,
            headers={"X-Api-Status-Code": "45000000"},
            json={"message": "private auth detail"},
        )

    result = asyncio.run(
        DoubaoASRService(doubao_config(), http_post=rejected).transcribe(
            b"RIFF-audio", "audio/wav"
        )
    )

    assert result.error_reason == "provider_rejected_request"
    assert calls == 1


@pytest.mark.parametrize(
    ("audio", "content_type", "expected"),
    [
        (b"", "audio/wav", "empty_audio"),
        (b"x" * (MAX_ASR_UPLOAD_BYTES + 1), "audio/wav", "audio_too_large"),
        (b"audio", "audio/flac", "unsupported_audio_type"),
    ],
)
def test_service_enforces_input_boundary(
    audio: bytes,
    content_type: str,
    expected: str,
) -> None:
    result = asyncio.run(
        DoubaoASRService(doubao_config()).transcribe(audio, content_type)
    )
    assert result.error_reason == expected


def test_rate_limiter_is_per_session_and_returns_retry_after() -> None:
    now = 100.0
    limiter = SessionASRRateLimiter(limit=2, window_seconds=60, clock=lambda: now)

    assert limiter.acquire("one").allowed
    assert limiter.acquire("one").allowed
    blocked = limiter.acquire("one")
    assert not blocked.allowed and blocked.retry_after_seconds == 60
    assert limiter.acquire("two").allowed


def test_transcription_endpoint_returns_text_without_mutating_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1.endpoints import sessions as sessions_endpoint

    repository = MagicMock()
    repository.get_session_by_uuid.return_value = SimpleNamespace(
        id=9,
        status="in_progress",
    )
    service = SimpleNamespace(
        transcribe=AsyncMock(
            return_value=ASRTranscriptionResult(
                request_id="request-api",
                text="转写后可编辑的文字。",
            )
        )
    )
    monkeypatch.setattr(sessions_endpoint, "SessionRepository", lambda _db: repository)
    monkeypatch.setattr(sessions_endpoint, "get_doubao_asr_service", lambda: service)
    monkeypatch.setattr(
        sessions_endpoint,
        "get_session_asr_rate_limiter",
        lambda: SessionASRRateLimiter(),
    )
    db = MagicMock()

    response = asyncio.run(
        sessions_endpoint.transcribe_session_speech(
            "public-session",
            raw_audio_request(b"RIFF-audio"),
            db,
        )
    )

    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload == {
        "text": "转写后可编辑的文字。",
        "provider": "doubao",
        "request_id": "request-api",
    }
    assert response.headers["x-asr-provider"] == "doubao"
    assert response.headers["x-request-id"] == "request-api"
    assert response.headers["cache-control"] == "no-store"
    service.transcribe.assert_awaited_once()
    assert service.transcribe.await_args.args == (b"RIFF-audio", "audio/wav")
    assert service.transcribe.await_args.kwargs["request_id"]
    db.add.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    ("session", "content_type", "status_code", "code"),
    [
        (None, "audio/wav", 404, "session_not_found"),
        (
            SimpleNamespace(id=9, status="completed"),
            "audio/wav",
            409,
            "session_not_active",
        ),
        (
            SimpleNamespace(id=9, status="in_progress"),
            "application/octet-stream",
            415,
            "unsupported_audio_type",
        ),
    ],
)
def test_transcription_endpoint_has_stable_session_and_mime_errors(
    monkeypatch: pytest.MonkeyPatch,
    session: Any,
    content_type: str,
    status_code: int,
    code: str,
) -> None:
    from app.api.v1.endpoints import sessions as sessions_endpoint

    repository = MagicMock()
    repository.get_session_by_uuid.return_value = session
    monkeypatch.setattr(sessions_endpoint, "SessionRepository", lambda _db: repository)
    monkeypatch.setattr(
        sessions_endpoint,
        "get_session_asr_rate_limiter",
        lambda: SessionASRRateLimiter(),
    )
    service_factory = MagicMock()
    monkeypatch.setattr(sessions_endpoint, "get_doubao_asr_service", service_factory)
    db = MagicMock()

    response = asyncio.run(
        sessions_endpoint.transcribe_session_speech(
            "public-session",
            raw_audio_request(b"audio", content_type),
            db,
        )
    )

    assert response.status_code == status_code
    payload = json.loads(response.body)
    assert payload["code"] == code
    assert payload["request_id"] == response.headers["x-request-id"]
    assert "private" not in response.body.decode("utf-8")
    service_factory.assert_not_called()
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_transcription_endpoint_stops_stream_at_five_megabytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1.endpoints import sessions as sessions_endpoint

    repository = MagicMock()
    repository.get_session_by_uuid.return_value = SimpleNamespace(
        id=9,
        status="in_progress",
    )
    monkeypatch.setattr(sessions_endpoint, "SessionRepository", lambda _db: repository)
    monkeypatch.setattr(
        sessions_endpoint,
        "get_session_asr_rate_limiter",
        lambda: SessionASRRateLimiter(),
    )
    service_factory = MagicMock()
    monkeypatch.setattr(sessions_endpoint, "get_doubao_asr_service", service_factory)

    response = asyncio.run(
        sessions_endpoint.transcribe_session_speech(
            "public-session",
            raw_audio_request(
                b"",
                chunks=[b"x" * MAX_ASR_UPLOAD_BYTES, b"overflow"],
            ),
            MagicMock(),
        )
    )

    assert response.status_code == 413
    assert json.loads(response.body)["code"] == "audio_too_large"
    service_factory.assert_not_called()


def test_sessions_router_exposes_plural_raw_transcription_path() -> None:
    from app.api.v1.endpoints.sessions import router

    route = next(
        route
        for route in router.routes
        if getattr(route, "path", "").endswith("/speech/transcriptions")
    )
    assert route.path == "/sessions/{session_uuid}/speech/transcriptions"
    assert route.methods == {"POST"}

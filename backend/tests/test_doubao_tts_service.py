from __future__ import annotations

import asyncio
import gzip
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.doubao_tts_service import (
    DEFAULT_DOUBAO_INTERVIEWER_SPEAKER,
    DOUBAO_V3_WEBSOCKET_URL,
    DoubaoProtocolError,
    DoubaoTTSService,
    InvalidPersistedAITurnError,
    PersistedAITurn,
    TTSConfig,
    TTSSynthesisResult,
    build_doubao_request_frame,
    parse_doubao_response_frame,
)


FULL_SERVER_RESPONSE = 0x9
AUDIO_ONLY_SERVER = 0xB
ERROR_MESSAGE = 0xF
WITH_EVENT = 0x4
TTS_SENTENCE_START = 350
TTS_SENTENCE_END = 351
TTS_RESPONSE = 352
SESSION_FINISHED = 152
VALID_MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x10\xc4" + b"\x00" * 32


def ai_turn(*, content: str = "我先确认相关方的约束。") -> PersistedAITurn:
    return PersistedAITurn(
        id=41,
        turn_index=7,
        speaker="ai",
        content=content,
        persisted=True,
    )


def event_frame(
    message_type: int,
    event: int,
    payload: bytes = b"",
    *,
    session_id: str = "provider-session",
    compression: int = 0,
) -> bytes:
    encoded_payload = gzip.compress(payload, mtime=0) if compression == 1 else payload
    encoded_session = session_id.encode("utf-8")
    return (
        bytes((0x11, (message_type << 4) | WITH_EVENT, 0x10 | compression, 0x00))
        + event.to_bytes(4, "big", signed=True)
        + len(encoded_session).to_bytes(4, "big")
        + encoded_session
        + len(encoded_payload).to_bytes(4, "big")
        + encoded_payload
    )


def error_frame(code: int, message: str) -> bytes:
    payload = message.encode("utf-8")
    return (
        bytes((0x11, ERROR_MESSAGE << 4, 0x10, 0x00))
        + code.to_bytes(4, "big")
        + len(payload).to_bytes(4, "big")
        + payload
    )


class FakeWebSocket:
    def __init__(self, messages: list[Any]) -> None:
        self.messages = list(messages)
        self.sent: list[bytes] = []
        self.recv_count = 0

    async def send(self, payload: bytes) -> None:
        self.sent.append(payload)

    async def recv(self) -> Any:
        self.recv_count += 1
        if not self.messages:
            raise RuntimeError("test stream exhausted")
        return self.messages.pop(0)


class HangingWebSocket(FakeWebSocket):
    async def recv(self) -> bytes:
        await asyncio.Future()
        raise AssertionError("unreachable")


class FakeConnection:
    def __init__(self, websocket: FakeWebSocket) -> None:
        self.websocket = websocket

    async def __aenter__(self) -> FakeWebSocket:
        return self.websocket

    async def __aexit__(self, *args: Any) -> None:
        return None


class FailingConnection:
    async def __aenter__(self) -> FakeWebSocket:
        raise OSError("transient private connection detail")

    async def __aexit__(self, *args: Any) -> None:
        return None


class CapturingConnectFactory:
    def __init__(self, websocket: FakeWebSocket) -> None:
        self.websocket = websocket
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, endpoint: str, **kwargs: Any) -> FakeConnection:
        self.calls.append((endpoint, kwargs))
        return FakeConnection(self.websocket)


class SequenceConnectFactory:
    def __init__(self, connections: list[Any]) -> None:
        self.connections = list(connections)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, endpoint: str, **kwargs: Any) -> Any:
        self.calls.append((endpoint, kwargs))
        return self.connections.pop(0)


def doubao_config(**overrides: Any) -> TTSConfig:
    values: dict[str, Any] = {
        "mode": "doubao",
        "api_key": "secret-new-api-key",
        "resource_id": "seed-tts-2.0",
        "speaker": DEFAULT_DOUBAO_INTERVIEWER_SPEAKER,
        "timeout_seconds": 0.5,
    }
    values.update(overrides)
    return TTSConfig(**values)


def decode_request(frame: bytes) -> dict[str, Any]:
    assert frame[:4] == bytes((0x11, 0x10, 0x10, 0x00))
    payload_size = int.from_bytes(frame[4:8], "big")
    assert payload_size == len(frame[8:])
    return json.loads(frame[8:].decode("utf-8"))


def success_messages(*, audio: bytes = VALID_MP3) -> list[bytes]:
    return [
        event_frame(FULL_SERVER_RESPONSE, TTS_SENTENCE_START, b'{"text":""}'),
        event_frame(AUDIO_ONLY_SERVER, TTS_RESPONSE, audio[:4]),
        event_frame(AUDIO_ONLY_SERVER, TTS_RESPONSE, audio[4:]),
        event_frame(FULL_SERVER_RESPONSE, TTS_SENTENCE_END, b'{"text":"done"}'),
        event_frame(FULL_SERVER_RESPONSE, SESSION_FINISHED, b'{"usage":{}}'),
    ]


def test_default_contract_uses_ruyayichen_and_fixed_official_v3_endpoint() -> None:
    config = doubao_config()

    assert config.speaker == "zh_male_ruyayichen_saturn_bigtts"
    assert config.sample_rate == 24_000
    assert config.bit_rate == 128_000
    assert config.timeout_seconds == 0.5
    assert config.max_attempts == 2
    assert "secret-new-api-key" not in repr(config)
    with pytest.raises(ValueError, match="official V3"):
        doubao_config(endpoint="wss://example.invalid/steal-key")


def test_request_payload_contains_only_ai_text_and_voice_parameters() -> None:
    config = doubao_config()
    frame = build_doubao_request_frame(
        config=config,
        ai_text="这是已持久化的 AI 访谈文本。",
    )

    payload = decode_request(frame)
    assert payload == {
        "req_params": {
            "speaker": "zh_male_ruyayichen_saturn_bigtts",
            "text": "这是已持久化的 AI 访谈文本。",
            "audio_params": {
                "format": "mp3",
                "sample_rate": 24_000,
                "bit_rate": 128_000,
                "speech_rate": -5,
            },
            "context_texts": [config.context_text],
        }
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    for forbidden in (
        "secret-new-api-key",
        "seed-tts-2.0",
        "session_uuid",
        "user_answer",
        "recording",
    ):
        assert forbidden not in serialized


def test_parser_handles_audio_final_and_gzip_frames() -> None:
    audio = parse_doubao_response_frame(
        event_frame(AUDIO_ONLY_SERVER, TTS_RESPONSE, b"first", compression=1)
    )
    final = parse_doubao_response_frame(
        event_frame(FULL_SERVER_RESPONSE, SESSION_FINISHED, b'{"usage":{}}')
    )

    assert audio.payload == b"first"
    assert audio.event == TTS_RESPONSE and not audio.is_final
    assert final.event == SESSION_FINISHED and final.is_final


@pytest.mark.parametrize(
    "frame",
    [
        b"",
        bytes((0x21, 0x94, 0x10, 0x00)),
        event_frame(AUDIO_ONLY_SERVER, TTS_RESPONSE, b"abc")[:-1],
        bytes((0x11, 0xD0, 0x10, 0x00)),
    ],
)
def test_malformed_v3_frames_fail_closed(frame: bytes) -> None:
    with pytest.raises(DoubaoProtocolError):
        parse_doubao_response_frame(frame)


def test_service_accepts_only_persisted_ai_turns() -> None:
    service = DoubaoTTSService(TTSConfig(mode="disabled"))
    with pytest.raises(InvalidPersistedAITurnError):
        asyncio.run(service.synthesize("用户回答"))  # type: ignore[arg-type]

    with pytest.raises(InvalidPersistedAITurnError):
        PersistedAITurn(
            id=1,
            turn_index=1,
            speaker="user",  # type: ignore[arg-type]
            content="这是用户回答，绝不能发送给 TTS。",
            persisted=True,
        )
    with pytest.raises(InvalidPersistedAITurnError):
        PersistedAITurn(
            id=1,
            turn_index=1,
            speaker="ai",
            content="draft",
            persisted=False,  # type: ignore[arg-type]
        )


def test_v3_combines_audio_uses_api_key_headers_and_one_final_event() -> None:
    websocket = FakeWebSocket(success_messages())
    connect = CapturingConnectFactory(websocket)
    service = DoubaoTTSService(
        doubao_config(),
        connect_factory=connect,
        request_id_factory=lambda: "request-123",
        connect_id_factory=lambda: "connect-456",
    )

    result = asyncio.run(service.synthesize(ai_turn()))

    assert result.ok and result.audio == VALID_MP3
    assert result.provider == "doubao" and result.request_id == "request-123"
    assert connect.calls[0][0] == DOUBAO_V3_WEBSOCKET_URL
    assert connect.calls[0][1]["additional_headers"] == {
        "X-Api-Key": "secret-new-api-key",
        "X-Api-Resource-Id": "seed-tts-2.0",
        "X-Api-Request-Id": "request-123",
        "X-Api-Connect-Id": "connect-456",
        "X-Control-Require-Usage-Tokens-Return": "*",
    }
    assert len(websocket.sent) == 1
    assert websocket.recv_count == 5
    assert sum(
        parse_doubao_response_frame(frame).event == SESSION_FINISHED
        for frame in success_messages()
    ) == 1
    assert decode_request(websocket.sent[0])["req_params"]["text"] == ai_turn().content


def test_transient_network_failure_gets_exactly_one_retry() -> None:
    second_websocket = FakeWebSocket(success_messages())
    connect = SequenceConnectFactory(
        [FailingConnection(), FakeConnection(second_websocket)]
    )
    request_ids = iter(["request-first", "request-second"])
    connect_ids = iter(["connect-first", "connect-second"])
    service = DoubaoTTSService(
        doubao_config(retry_delay_seconds=0),
        connect_factory=connect,
        request_id_factory=lambda: next(request_ids),
        connect_id_factory=lambda: next(connect_ids),
    )

    result = asyncio.run(service.synthesize(ai_turn()))

    assert result.ok and result.request_id == "request-second"
    assert len(connect.calls) == 2


def test_timeout_retries_once_then_returns_stable_fallback() -> None:
    connect = CapturingConnectFactory(HangingWebSocket([]))
    service = DoubaoTTSService(
        doubao_config(timeout_seconds=0.01, retry_delay_seconds=0),
        connect_factory=connect,
        request_id_factory=lambda: "timeout-request",
    )

    result = asyncio.run(service.synthesize(ai_turn()))

    assert result.audio is None and result.fallback_required
    assert result.fallback_reason == "provider_timeout"
    assert result.request_id == "timeout-request"
    assert len(connect.calls) == 2


def test_provider_rejection_is_not_retried_or_exposed() -> None:
    websocket = FakeWebSocket(
        [error_frame(45_000_000, "private provider detail with request text")]
    )
    connect = CapturingConnectFactory(websocket)
    service = DoubaoTTSService(
        doubao_config(),
        connect_factory=connect,
        request_id_factory=lambda: "rejected-request",
    )

    result = asyncio.run(service.synthesize(ai_turn()))

    assert result.fallback_required
    assert result.fallback_reason == "provider_rejected_request"
    assert "private provider detail" not in repr(result)
    assert "secret-new-api-key" not in repr(result)
    assert len(connect.calls) == 1


def test_disabled_missing_credentials_and_bad_stream_have_stable_reasons() -> None:
    disabled = asyncio.run(
        DoubaoTTSService(TTSConfig(mode="disabled")).synthesize(ai_turn())
    )
    missing = asyncio.run(
        DoubaoTTSService(TTSConfig(mode="doubao")).synthesize(ai_turn())
    )
    malformed = asyncio.run(
        DoubaoTTSService(
            doubao_config(),
            connect_factory=CapturingConnectFactory(FakeWebSocket([b"bad"])),
        ).synthesize(ai_turn())
    )

    assert disabled.fallback_reason == "tts_disabled"
    assert missing.fallback_reason == "credentials_missing"
    assert malformed.fallback_reason == "provider_protocol_error"


@pytest.mark.parametrize(
    "messages",
    [
        [event_frame(FULL_SERVER_RESPONSE, SESSION_FINISHED, b'{"usage":{}}')],
        success_messages(audio=b"not-an-mp3"),
    ],
)
def test_final_without_valid_mp3_returns_protocol_fallback(
    messages: list[bytes],
) -> None:
    result = asyncio.run(
        DoubaoTTSService(
            doubao_config(),
            connect_factory=CapturingConnectFactory(FakeWebSocket(messages)),
        ).synthesize(ai_turn())
    )

    assert result.audio is None
    assert result.fallback_reason == "provider_protocol_error"


def test_speech_endpoint_returns_mp3_headers_for_persisted_ai_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1.endpoints import sessions as sessions_endpoint

    repository = MagicMock()
    repository.get_session_by_uuid.return_value = SimpleNamespace(id=9)
    repository.get_turn_by_index.return_value = SimpleNamespace(
        id=31,
        turn_index=4,
        speaker="ai",
        content="已持久化的问题。",
    )
    service = SimpleNamespace(
        synthesize=AsyncMock(
            return_value=TTSSynthesisResult(
                audio=b"mp3",
                content_type="audio/mpeg",
                provider="doubao",
                fallback_required=False,
                request_id="request-api",
            )
        )
    )
    monkeypatch.setattr(sessions_endpoint, "SessionRepository", lambda _db: repository)
    monkeypatch.setattr(sessions_endpoint, "get_doubao_tts_service", lambda: service)
    db = MagicMock()

    response = asyncio.run(
        sessions_endpoint.get_turn_speech("session-public", 4, db)
    )

    assert response.status_code == 200
    assert response.media_type == "audio/mpeg"
    assert response.body == b"mp3"
    assert response.headers["x-tts-provider"] == "doubao"
    assert response.headers["x-tts-request-id"] == "request-api"
    assert response.headers["cache-control"] == "no-store"
    persisted_turn = service.synthesize.await_args.args[0]
    assert persisted_turn == PersistedAITurn(
        id=31,
        turn_index=4,
        speaker="ai",
        content="已持久化的问题。",
        persisted=True,
    )
    db.add.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize("turn", [None, SimpleNamespace(speaker="user", content="用户回答")])
def test_speech_endpoint_hides_missing_and_non_ai_turns(
    monkeypatch: pytest.MonkeyPatch,
    turn: Any,
) -> None:
    from app.api.v1.endpoints import sessions as sessions_endpoint

    repository = MagicMock()
    repository.get_session_by_uuid.return_value = SimpleNamespace(id=9)
    repository.get_turn_by_index.return_value = turn
    service_factory = MagicMock()
    monkeypatch.setattr(sessions_endpoint, "SessionRepository", lambda _db: repository)
    monkeypatch.setattr(sessions_endpoint, "get_doubao_tts_service", service_factory)

    response = asyncio.run(
        sessions_endpoint.get_turn_speech("session-public", 4, MagicMock())
    )

    assert response.status_code == 404
    assert json.loads(response.body) == {
        "code": "assistant_turn_not_found",
        "message": "指定的已持久化 AI 回合不存在。",
    }
    service_factory.assert_not_called()


def test_speech_endpoint_returns_stable_503_without_mutating_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1.endpoints import sessions as sessions_endpoint

    repository = MagicMock()
    repository.get_session_by_uuid.return_value = SimpleNamespace(id=9)
    repository.get_turn_by_index.return_value = SimpleNamespace(
        id=31,
        turn_index=4,
        speaker="ai",
        content="已持久化的问题。",
    )
    service = SimpleNamespace(
        synthesize=AsyncMock(
            return_value=TTSSynthesisResult(
                audio=None,
                content_type="audio/mpeg",
                provider="doubao",
                fallback_required=True,
                fallback_reason="provider_timeout",
                request_id="request-timeout",
            )
        )
    )
    monkeypatch.setattr(sessions_endpoint, "SessionRepository", lambda _db: repository)
    monkeypatch.setattr(sessions_endpoint, "get_doubao_tts_service", lambda: service)
    db = MagicMock()

    response = asyncio.run(
        sessions_endpoint.get_turn_speech("session-public", 4, db)
    )

    assert response.status_code == 503
    assert json.loads(response.body) == {
        "code": "tts_fallback_required",
        "message": "服务端语音暂时不可用，请使用浏览器语音播报。",
        "reason": "provider_timeout",
    }
    assert response.headers["x-tts-fallback"] == "browser"
    assert response.headers["x-tts-request-id"] == "request-timeout"
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_speech_endpoint_maps_invalid_configuration_to_stable_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1.endpoints import sessions as sessions_endpoint

    repository = MagicMock()
    repository.get_session_by_uuid.return_value = SimpleNamespace(id=9)
    repository.get_turn_by_index.return_value = SimpleNamespace(
        id=31,
        turn_index=4,
        speaker="ai",
        content="已持久化的问题。",
    )
    monkeypatch.setattr(sessions_endpoint, "SessionRepository", lambda _db: repository)
    monkeypatch.setattr(
        sessions_endpoint,
        "get_doubao_tts_service",
        MagicMock(side_effect=ValueError("private invalid setting")),
    )

    response = asyncio.run(
        sessions_endpoint.get_turn_speech("session-public", 4, MagicMock())
    )

    assert response.status_code == 503
    assert json.loads(response.body) == {
        "code": "tts_fallback_required",
        "message": "服务端语音暂时不可用，请使用浏览器语音播报。",
        "reason": "configuration_invalid",
    }
    assert response.headers["x-tts-fallback"] == "browser"

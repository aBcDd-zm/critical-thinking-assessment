from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
from starlette.requests import ClientDisconnect
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories.session_repository import SessionRepository
from app.schemas.session import (
    ContinueStageResponse,
    CreateSessionRequest,
    FeedbackResponse,
    FeedbackStateResponse,
    FinishSessionResponse,
    LanguageModeResponse,
    PreparationResponse,
    ProfileTurnRequest,
    ReportResponse,
    ReportGenerationResponse,
    SessionResponse,
    SkipStageResponse,
    SubmitFeedbackRequest,
    SubmitTurnRequest,
    SubmitTurnResponse,
    UpdateLanguageModeRequest,
)
from app.services.scenario_generation_service import (
    ScenarioGenerationService,
    finalize_scenario_background,
    run_base_generation_background,
)
from app.services.doubao_tts_service import (
    PersistedAITurn,
    get_doubao_tts_service,
)
from app.services.doubao_asr_service import (
    MAX_ASR_UPLOAD_BYTES,
    ASRTranscriptionResult,
    get_doubao_asr_service,
    get_session_asr_rate_limiter,
    is_supported_audio_content_type,
)
from app.services.session_service import (
    SessionService,
    generate_completed_session_report_background,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _asr_json_response(
    *,
    status_code: int,
    request_id: str,
    code: str,
    message: str,
    reason: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> JSONResponse:
    content = {
        "code": code,
        "message": message,
        "request_id": request_id,
    }
    if reason:
        content["reason"] = reason
    headers = {
        "Cache-Control": "no-store",
        "X-Request-ID": request_id,
    }
    if extra_headers:
        headers.update(extra_headers)
    return JSONResponse(status_code=status_code, content=content, headers=headers)


def _asr_failure_response(result: ASRTranscriptionResult) -> JSONResponse:
    reason = result.error_reason or "provider_protocol_error"
    if reason == "provider_timeout":
        return _asr_json_response(
            status_code=504,
            request_id=result.request_id,
            code="asr_timeout",
            message="语音转写超时，请稍后重试。",
            reason=reason,
        )
    if reason == "no_speech_detected":
        return _asr_json_response(
            status_code=422,
            request_id=result.request_id,
            code="asr_no_speech",
            message="没有识别到清晰语音，请重新录制。",
            reason=reason,
        )
    if reason in {
        "asr_disabled",
        "credentials_missing",
        "converter_unavailable",
        "conversion_timeout",
        "conversion_failed",
        "provider_network_error",
    }:
        return _asr_json_response(
            status_code=503,
            request_id=result.request_id,
            code="asr_unavailable",
            message="语音转写暂时不可用，请稍后重试或直接输入文字。",
            reason=reason,
        )
    return _asr_json_response(
        status_code=502,
        request_id=result.request_id,
        code="asr_transcription_failed",
        message="语音转写失败，请重新录制或直接输入文字。",
        reason=reason,
    )


@router.post("", response_model=SessionResponse)
def create_session(
    payload: CreateSessionRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> SessionResponse:
    service = SessionService(db)
    response = service.create_session(payload)
    if response.flow_version not in {"progressive_v3_2", "progressive_v3_3"}:
        background_tasks.add_task(
            run_base_generation_background,
            service.get_session_id(response.session_uuid),
        )
    return response


@router.get("/{session_uuid}", response_model=SessionResponse)
def get_session(
    session_uuid: str,
    db: Session = Depends(get_db),
) -> SessionResponse:
    return SessionService(db).get_session(session_uuid)


@router.get(
    "/{session_uuid}/turns/{turn_index}/speech",
    responses={
        200: {"content": {"audio/mpeg": {}}},
        404: {"description": "已持久化 AI 回合不存在"},
        503: {"description": "需要使用浏览器语音回退"},
    },
)
async def get_turn_speech(
    session_uuid: str,
    turn_index: int,
    db: Session = Depends(get_db),
) -> Response:
    repository = SessionRepository(db)
    session = repository.get_session_by_uuid(session_uuid)
    if session is None:
        return JSONResponse(
            status_code=404,
            content={
                "code": "assistant_turn_not_found",
                "message": "指定的已持久化 AI 回合不存在。",
            },
        )
    turn = repository.get_turn_by_index(session.id, turn_index)
    if turn is None or turn.speaker != "ai" or not turn.content.strip():
        return JSONResponse(
            status_code=404,
            content={
                "code": "assistant_turn_not_found",
                "message": "指定的已持久化 AI 回合不存在。",
            },
        )

    try:
        speech_service = get_doubao_tts_service()
    except Exception:  # Invalid local configuration must not leak as a 500.
        return JSONResponse(
            status_code=503,
            content={
                "code": "tts_fallback_required",
                "message": "服务端语音暂时不可用，请使用浏览器语音播报。",
                "reason": "configuration_invalid",
            },
            headers={"X-TTS-Fallback": "browser"},
        )

    speech = await speech_service.synthesize(
        PersistedAITurn(
            id=turn.id,
            turn_index=turn.turn_index,
            speaker="ai",
            content=turn.content,
            persisted=True,
        )
    )
    if not speech.ok:
        headers = {"X-TTS-Fallback": "browser"}
        if speech.request_id:
            headers["X-TTS-Request-ID"] = speech.request_id
        return JSONResponse(
            status_code=503,
            content={
                "code": "tts_fallback_required",
                "message": "服务端语音暂时不可用，请使用浏览器语音播报。",
                "reason": speech.fallback_reason or "tts_unavailable",
            },
            headers=headers,
        )

    headers = {
        "Cache-Control": "no-store",
        "X-TTS-Provider": "doubao",
        "X-TTS-Request-ID": speech.request_id or "",
    }
    return Response(
        content=speech.audio,
        media_type=speech.content_type,
        headers=headers,
    )


@router.post(
    "/{session_uuid}/speech/transcriptions",
    responses={
        200: {"description": "豆包语音转写完成"},
        400: {"description": "音频为空"},
        404: {"description": "会话不存在"},
        409: {"description": "会话已结束"},
        413: {"description": "音频超过产品限制"},
        415: {"description": "不支持的音频格式"},
        429: {"description": "单会话请求过于频繁"},
        502: {"description": "供应商拒绝或响应不合法"},
        503: {"description": "语音转写暂时不可用"},
        504: {"description": "语音转写超时"},
    },
)
async def transcribe_session_speech(
    session_uuid: str,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    """Transcribe one short in-memory recording without changing session state."""

    request_id = str(uuid4())
    repository = SessionRepository(db)
    session = repository.get_session_by_uuid(session_uuid)
    if session is None:
        return _asr_json_response(
            status_code=404,
            request_id=request_id,
            code="session_not_found",
            message="测评会话不存在。",
        )
    if session.status == "completed":
        return _asr_json_response(
            status_code=409,
            request_id=request_id,
            code="session_not_active",
            message="测评会话已结束，不能继续语音输入。",
        )

    rate_limit = get_session_asr_rate_limiter().acquire(session_uuid)
    if not rate_limit.allowed:
        return _asr_json_response(
            status_code=429,
            request_id=request_id,
            code="asr_rate_limited",
            message="语音转写请求过于频繁，请稍后再试。",
            extra_headers={"Retry-After": str(rate_limit.retry_after_seconds)},
        )

    content_type = request.headers.get("content-type", "")
    if not is_supported_audio_content_type(content_type):
        return _asr_json_response(
            status_code=415,
            request_id=request_id,
            code="unsupported_audio_type",
            message="当前录音格式不受支持，请更换浏览器后重试。",
        )

    audio_parts: list[bytes] = []
    audio_size = 0
    try:
        async for chunk in request.stream():
            if not chunk:
                continue
            audio_size += len(chunk)
            if audio_size > MAX_ASR_UPLOAD_BYTES:
                return _asr_json_response(
                    status_code=413,
                    request_id=request_id,
                    code="audio_too_large",
                    message="单次录音不能超过 5MB，请缩短录音后重试。",
                )
            audio_parts.append(chunk)
    except ClientDisconnect:
        return _asr_json_response(
            status_code=400,
            request_id=request_id,
            code="audio_upload_interrupted",
            message="录音上传中断，请重新录制。",
        )
    if not audio_parts:
        return _asr_json_response(
            status_code=400,
            request_id=request_id,
            code="empty_audio",
            message="没有收到录音内容，请重新录制。",
        )

    try:
        asr_service = get_doubao_asr_service()
    except Exception:
        return _asr_json_response(
            status_code=503,
            request_id=request_id,
            code="asr_unavailable",
            message="语音转写暂时不可用，请稍后重试或直接输入文字。",
            reason="configuration_invalid",
        )

    result = await asr_service.transcribe(
        b"".join(audio_parts),
        content_type,
        request_id=request_id,
    )
    if not result.ok:
        return _asr_failure_response(result)

    return JSONResponse(
        status_code=200,
        content={
            "text": result.text,
            "provider": "doubao",
            "request_id": result.request_id,
        },
        headers={
            "Cache-Control": "no-store",
            "X-ASR-Provider": "doubao",
            "X-Request-ID": result.request_id,
        },
    )


@router.get("/{session_uuid}/preparation", response_model=PreparationResponse)
def get_preparation(
    session_uuid: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> PreparationResponse:
    session_service = SessionService(db)
    session_id = session_service.get_session_id(session_uuid)
    response = session_service.get_preparation(session_uuid)
    session = session_service.get_session(session_uuid)
    if session.flow_version not in {"progressive_v3_2", "progressive_v3_3"}:
        generation_service = ScenarioGenerationService(db)
        if generation_service.resume_if_stale(session_id):
            background_tasks.add_task(run_base_generation_background, session_id)
        background_tasks.add_task(finalize_scenario_background, session_id)
    return response


@router.post("/{session_uuid}/profile/turns/stream")
def submit_profile_turn_stream(
    session_uuid: str,
    payload: ProfileTurnRequest,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    service = SessionService(db)
    session_id = service.get_session_id(session_uuid)
    session = service.get_session(session_uuid)
    return StreamingResponse(
        service.stream_profile_turn(session_uuid, payload),
        media_type="application/x-ndjson",
        background=(
            None
            if session.flow_version in {"progressive_v3_2", "progressive_v3_3"}
            else BackgroundTask(finalize_scenario_background, session_id)
        ),
    )


@router.post("/{session_uuid}/interview/start/stream")
def start_interview_stream(
    session_uuid: str,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    return StreamingResponse(
        SessionService(db).stream_start_interview(session_uuid),
        media_type="application/x-ndjson",
    )


@router.post("/{session_uuid}/turns", response_model=SubmitTurnResponse)
def submit_turn(
    session_uuid: str,
    payload: SubmitTurnRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> SubmitTurnResponse:
    response = SessionService(db).submit_turn(session_uuid, payload)
    background_tasks.add_task(
        generate_completed_session_report_background,
        session_uuid,
    )
    return response


@router.post("/{session_uuid}/turns/stream")
def submit_turn_stream(
    session_uuid: str,
    payload: SubmitTurnRequest,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    return StreamingResponse(
        SessionService(db).stream_submit_turn(session_uuid, payload),
        media_type="application/x-ndjson",
        background=BackgroundTask(
            generate_completed_session_report_background,
            session_uuid,
        ),
    )


@router.post("/{session_uuid}/finish", response_model=FinishSessionResponse)
def finish_session(
    session_uuid: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> FinishSessionResponse:
    response = SessionService(db).finish_session(session_uuid)
    background_tasks.add_task(
        generate_completed_session_report_background,
        session_uuid,
    )
    return response


@router.post("/{session_uuid}/stages/current/skip", response_model=SkipStageResponse)
def skip_current_stage(
    session_uuid: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> SkipStageResponse:
    response = SessionService(db).skip_current_stage(session_uuid)
    background_tasks.add_task(
        generate_completed_session_report_background,
        session_uuid,
    )
    return response


@router.post(
    "/{session_uuid}/stages/current/continue",
    response_model=ContinueStageResponse,
)
def continue_current_stage(
    session_uuid: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> ContinueStageResponse:
    response = SessionService(db).continue_current_stage(session_uuid)
    background_tasks.add_task(
        generate_completed_session_report_background,
        session_uuid,
    )
    return response


@router.patch(
    "/{session_uuid}/language-mode",
    response_model=LanguageModeResponse,
)
def update_language_mode(
    session_uuid: str,
    payload: UpdateLanguageModeRequest,
    db: Session = Depends(get_db),
) -> LanguageModeResponse:
    return SessionService(db).update_language_mode(session_uuid, payload)


@router.post("/{session_uuid}/feedback", response_model=FeedbackResponse)
def submit_feedback(
    session_uuid: str,
    payload: SubmitFeedbackRequest,
    db: Session = Depends(get_db),
) -> FeedbackResponse:
    return SessionService(db).submit_feedback(session_uuid, payload)


@router.get("/{session_uuid}/feedback", response_model=FeedbackStateResponse)
def get_feedback(
    session_uuid: str,
    db: Session = Depends(get_db),
) -> FeedbackStateResponse:
    return SessionService(db).get_feedback(session_uuid)


@router.get("/{session_uuid}/report", response_model=ReportResponse)
def get_report(
    session_uuid: str,
    db: Session = Depends(get_db),
) -> ReportResponse:
    return SessionService(db).get_report(session_uuid)


@router.post(
    "/{session_uuid}/report/generate",
    response_model=ReportGenerationResponse,
    status_code=202,
)
def request_report_generation(
    session_uuid: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> ReportGenerationResponse:
    response = SessionService(db).request_report_generation(session_uuid)
    if response.status == "scheduled":
        background_tasks.add_task(
            generate_completed_session_report_background,
            session_uuid,
        )
    return response


@router.get(
    "/{session_uuid}/report.pdf",
    response_class=Response,
    responses={
        200: {
            "description": "正式中文 PDF 测评报告",
            "content": {
                "application/pdf": {
                    "schema": {"type": "string", "format": "binary"},
                }
            },
        }
    },
)
def download_report_pdf(
    session_uuid: str,
    timezone_name: str = Query(
        default="Asia/Shanghai",
        alias="timezone",
        min_length=1,
        max_length=64,
    ),
    db: Session = Depends(get_db),
) -> Response:
    content, filename = SessionService(db).get_report_pdf(
        session_uuid,
        timezone_name=timezone_name,
    )
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                "attachment; filename=critical-thinking-report.pdf; "
                f"filename*=UTF-8''{quote(filename)}"
            ),
            "Cache-Control": "private, no-store",
        },
    )

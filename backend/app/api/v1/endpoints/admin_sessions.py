import json
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.v1.endpoints.admin import AdminDep
from app.core.database import get_db
from app.schemas.admin_review import (
    AdminSessionListResponse,
    AdminSessionReviewResponse,
    ExpertScoreBatchRequest,
    ExpertScoreBatchResponse,
    HumanReviewOut,
    HumanReviewUpdate,
)
from app.services.admin_session_review_service import AdminSessionReviewService


router = APIRouter(prefix="/admin", tags=["admin-session-review"])


@router.get("/sessions", response_model=AdminSessionListResponse)
def list_sessions(
    admin: AdminDep,
    db: Annotated[Session, Depends(get_db)],
    status: str | None = Query(default=None, max_length=32),
    scenario_code: str | None = Query(default=None, max_length=64),
    search: str | None = Query(default=None, max_length=128),
    review_status: Literal[
        "pending", "in_review", "completed", "needs_adjudication"
    ]
    | None = Query(default=None),
    low_confidence: bool = Query(default=False),
    confidence_threshold: float = Query(default=0.5, ge=0, le=1),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> AdminSessionListResponse:
    return AdminSessionReviewService(db).list_sessions(
        status_value=status,
        scenario_code=scenario_code,
        search=search,
        review_status=review_status,
        low_confidence=low_confidence,
        confidence_threshold=confidence_threshold,
        annotator_id=admin.id,
        page=page,
        page_size=page_size,
    )


@router.get("/sessions/export")
def export_sessions(
    _admin: AdminDep,
    db: Annotated[Session, Depends(get_db)],
    format: Literal["json", "csv_zip"] = Query(default="json"),
    status: str | None = Query(default="completed", max_length=32),
    scenario_code: str | None = Query(default=None, max_length=64),
    search: str | None = Query(default=None, max_length=128),
    review_status: Literal[
        "pending", "in_review", "completed", "needs_adjudication"
    ]
    | None = Query(default=None),
    low_confidence: bool = Query(default=False),
    confidence_threshold: float = Query(default=0.5, ge=0, le=1),
) -> Response:
    service = AdminSessionReviewService(db)
    payload = service.build_export(
        status_value=status,
        scenario_code=scenario_code,
        search=search,
        review_status=review_status,
        low_confidence=low_confidence,
        confidence_threshold=confidence_threshold,
    )
    date_label = datetime.now().strftime("%Y-%m-%d")
    if format == "csv_zip":
        return Response(
            content=service.build_csv_zip(payload),
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="assessment-research-export-{date_label}.zip"'
                )
            },
        )
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="assessment-research-export-{date_label}.json"'
            )
        },
    )


@router.get("/sessions/{session_uuid}/review", response_model=AdminSessionReviewResponse)
def get_session_review(
    session_uuid: str,
    admin: AdminDep,
    db: Annotated[Session, Depends(get_db)],
) -> AdminSessionReviewResponse:
    return AdminSessionReviewService(db).get_review(
        session_uuid,
        current_annotator_id=admin.id,
    )


@router.put(
    "/sessions/{session_uuid}/human-review",
    response_model=HumanReviewOut,
)
def update_human_review(
    session_uuid: str,
    payload: HumanReviewUpdate,
    admin: AdminDep,
    db: Annotated[Session, Depends(get_db)],
) -> HumanReviewOut:
    return AdminSessionReviewService(db).save_human_review(
        session_uuid,
        reviewer=admin,
        payload=payload,
    )


@router.put(
    "/sessions/{session_uuid}/expert-scores",
    response_model=ExpertScoreBatchResponse,
)
def save_expert_scores(
    session_uuid: str,
    payload: ExpertScoreBatchRequest,
    admin: AdminDep,
    db: Annotated[Session, Depends(get_db)],
) -> ExpertScoreBatchResponse:
    return AdminSessionReviewService(db).save_expert_scores(
        session_uuid,
        annotator=admin,
        items=payload.items,
    )


@router.post(
    "/expert-scores/import",
    response_model=ExpertScoreBatchResponse,
)
def import_expert_scores(
    csv_content: Annotated[bytes, Body(media_type="text/csv")],
    admin: AdminDep,
    db: Annotated[Session, Depends(get_db)],
) -> ExpertScoreBatchResponse:
    return AdminSessionReviewService(db).import_expert_scores(
        csv_content,
        annotator=admin,
    )

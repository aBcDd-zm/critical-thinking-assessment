from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status as http_status
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.admin import AdminUser
from app.models.agent import AgentTrace
from app.models.assessment import AssessmentSession, DialogueTurn
from app.models.feedback import SessionFeedback
from app.models.participant import Participant
from app.models.report import AssessmentReport
from app.models.review import ExpertScoreAnnotation, HumanReview
from app.models.rubric import RubricDimension
from app.models.scenario import (
    Scenario,
    ScenarioGenerationJob,
    ScenarioStage,
    ScenarioStageDimension,
    StageDynamicInfo,
    StageInterventionRule,
)
from app.models.scoring import ScoreEvidence, ScoreResult, ScoreSnapshot
from app.schemas.admin_review import (
    AdminReviewEvidence,
    AdminReviewFeedback,
    AdminReviewReport,
    AdminReviewScoreResult,
    AdminReviewScoreSnapshot,
    AdminReviewSession,
    AdminReviewTrace,
    AdminReviewTurn,
    AdminSessionListItem,
    AdminSessionListResponse,
    AdminSessionReviewResponse,
    ExpertScoreBatchResponse,
    ExpertScoreOut,
    ExpertScoreTarget,
    ExpertScoreWrite,
    HumanReviewOut,
    HumanReviewUpdate,
)
from app.services.evidence_sufficiency_service import EvidenceSufficiencyService

LOW_CONFIDENCE_THRESHOLD = 0.5
TRACE_VALIDATION_CODE_ALLOWLIST = {
    "clinical_role_claim",
    "duplicate_question",
    "fabricated_self_disclosure",
    "fact_code",
    "internal_terms",
    "invalid_json",
    "judgmental",
    "leading",
    "missing_reflection",
    "missing_selected_fact",
    "planner_action_enforced",
    "prescriptive_authority",
    "prompt_template_missing",
    "quality_flags",
    "question_count",
    "reflection_quote_ids",
    "relational_attachment",
    "renderer_budget_exhausted",
    "renderer_exception",
    "role_substitution",
    "semantic_duplicate_question",
    "too_long",
    "too_many_sentences",
    "unexpected_fact",
    "ungrounded_reflection",
    "unreleased_fact",
    "unsupported_hidden_meaning",
    "unsupported_inference",
}
TRACE_VALIDATION_CODE_PREFIXES = (
    "forbidden_inferred_role:",
    "unreleased_fact_text:",
)


class AdminSessionReviewService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_sessions(
        self,
        *,
        status_value: str | None,
        scenario_code: str | None,
        search: str | None,
        review_status: str | None,
        low_confidence: bool,
        confidence_threshold: float,
        annotator_id: int,
        page: int,
        page_size: int,
    ) -> AdminSessionListResponse:
        conditions = self._session_conditions(
            status_value,
            scenario_code,
            search,
            review_status,
            low_confidence,
            confidence_threshold,
        )
        total = int(
            self.db.execute(
                select(func.count())
                .select_from(AssessmentSession)
                .join(Participant, Participant.id == AssessmentSession.participant_id)
                .join(Scenario, Scenario.id == AssessmentSession.scenario_id)
                .outerjoin(HumanReview, HumanReview.session_id == AssessmentSession.id)
                .where(*conditions)
            ).scalar_one()
        )

        turn_counts = (
            select(DialogueTurn.session_id, func.count(DialogueTurn.id).label("turn_count"))
            .group_by(DialogueTurn.session_id)
            .subquery()
        )
        trace_counts = (
            select(AgentTrace.session_id, func.count(AgentTrace.id).label("trace_count"))
            .group_by(AgentTrace.session_id)
            .subquery()
        )
        latest_final_snapshot_id = (
            select(func.max(ScoreSnapshot.id))
            .where(
                ScoreSnapshot.session_id == AssessmentSession.id,
                ScoreSnapshot.snapshot_type == "final",
            )
            .correlate(AssessmentSession)
            .scalar_subquery()
        )
        min_ai_confidence = (
            select(func.min(ScoreResult.confidence))
            .where(ScoreResult.snapshot_id == latest_final_snapshot_id)
            .correlate(AssessmentSession)
            .scalar_subquery()
        )
        expert_score_count = (
            select(func.count(ExpertScoreAnnotation.id))
            .where(
                ExpertScoreAnnotation.session_id == AssessmentSession.id,
                ExpertScoreAnnotation.annotator_id == annotator_id,
            )
            .correlate(AssessmentSession)
            .scalar_subquery()
        )
        expert_target_count = (
            select(func.count(ScenarioStageDimension.id))
            .join(ScenarioStage, ScenarioStage.id == ScenarioStageDimension.stage_id)
            .where(ScenarioStage.scenario_id == AssessmentSession.scenario_id)
            .correlate(AssessmentSession)
            .scalar_subquery()
        )
        rows = self.db.execute(
            select(
                AssessmentSession,
                Participant.nickname,
                Scenario.scenario_code,
                Scenario.title,
                func.coalesce(turn_counts.c.turn_count, 0),
                func.coalesce(trace_counts.c.trace_count, 0),
                AssessmentReport.status,
                func.coalesce(HumanReview.status, "pending"),
                HumanReview.decision,
                min_ai_confidence,
                expert_score_count,
                expert_target_count,
            )
            .join(Participant, Participant.id == AssessmentSession.participant_id)
            .join(Scenario, Scenario.id == AssessmentSession.scenario_id)
            .outerjoin(turn_counts, turn_counts.c.session_id == AssessmentSession.id)
            .outerjoin(trace_counts, trace_counts.c.session_id == AssessmentSession.id)
            .outerjoin(AssessmentReport, AssessmentReport.session_id == AssessmentSession.id)
            .outerjoin(HumanReview, HumanReview.session_id == AssessmentSession.id)
            .where(*conditions)
            .order_by(AssessmentSession.updated_at.desc(), AssessmentSession.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()

        items = [
            AdminSessionListItem(
                session_uuid=session.session_uuid,
                nickname=nickname,
                scenario_code=row_scenario_code,
                scenario_title=scenario_title,
                status=session.status,
                assessment_mode=session.assessment_mode,
                flow_version=session.flow_version,
                state_version=session.state_version or 0,
                turn_count=int(turn_count),
                agent_trace_count=int(trace_count),
                report_status=report_status,
                review_status=row_review_status,
                review_decision=review_decision,
                min_ai_confidence=(
                    float(row_min_ai_confidence)
                    if row_min_ai_confidence is not None
                    else None
                ),
                expert_score_count=int(row_expert_score_count),
                expert_score_target_count=int(row_expert_target_count),
                expert_score_completion_rate=(
                    round(
                        int(row_expert_score_count)
                        / int(row_expert_target_count)
                        * 100,
                        1,
                    )
                    if row_expert_target_count
                    else 0.0
                ),
                duration_minutes=_duration_minutes(session.total_duration_seconds),
                started_at=session.started_at,
                completed_at=session.completed_at,
                updated_at=session.updated_at,
            )
            for (
                session,
                nickname,
                row_scenario_code,
                scenario_title,
                turn_count,
                trace_count,
                report_status,
                row_review_status,
                review_decision,
                row_min_ai_confidence,
                row_expert_score_count,
                row_expert_target_count,
            ) in rows
        ]
        return AdminSessionListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )

    def get_review(
        self,
        session_uuid: str,
        *,
        current_annotator_id: int,
    ) -> AdminSessionReviewResponse:
        row = self.db.execute(
            select(AssessmentSession, Participant, Scenario, ScenarioStage)
            .join(Participant, Participant.id == AssessmentSession.participant_id)
            .join(Scenario, Scenario.id == AssessmentSession.scenario_id)
            .outerjoin(ScenarioStage, ScenarioStage.id == AssessmentSession.current_stage_id)
            .where(AssessmentSession.session_uuid == session_uuid)
        ).one_or_none()
        if row is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Assessment session not found",
            )
        session, participant, scenario, current_stage = row
        stage_map = self._stage_map(scenario.id)
        rule_map, info_map = self._rule_and_info_maps(stage_map)

        turns = self.db.execute(
            select(DialogueTurn)
            .where(DialogueTurn.session_id == session.id)
            .order_by(DialogueTurn.turn_index)
        ).scalars().all()
        traces = self.db.execute(
            select(AgentTrace)
            .where(AgentTrace.session_id == session.id)
            .order_by(AgentTrace.created_at, AgentTrace.id)
        ).scalars().all()
        trace_audit = {
            trace.id: _trace_audit_fields(trace.config_snapshot_json)
            for trace in traces
        }
        snapshots = self.db.execute(
            select(ScoreSnapshot)
            .where(ScoreSnapshot.session_id == session.id)
            .order_by(ScoreSnapshot.created_at, ScoreSnapshot.id)
        ).scalars().all()
        snapshot_ids = [item.id for item in snapshots]
        result_rows = (
            self.db.execute(
                select(ScoreResult, RubricDimension)
                .join(RubricDimension, RubricDimension.id == ScoreResult.dimension_id)
                .where(ScoreResult.snapshot_id.in_(snapshot_ids))
                .order_by(ScoreResult.id)
            ).all()
            if snapshot_ids
            else []
        )
        result_ids = [result.id for result, _dimension in result_rows]
        evidence_rows = (
            self.db.execute(
                select(ScoreEvidence)
                .where(ScoreEvidence.score_result_id.in_(result_ids))
                .order_by(ScoreEvidence.id)
            ).scalars().all()
            if result_ids
            else []
        )
        evidence_by_result: dict[int, list[ScoreEvidence]] = defaultdict(list)
        for evidence in evidence_rows:
            evidence_by_result[evidence.score_result_id].append(evidence)
        results_by_snapshot: dict[int, list[AdminReviewScoreResult]] = defaultdict(list)
        for result, dimension in result_rows:
            results_by_snapshot[result.snapshot_id].append(
                AdminReviewScoreResult(
                    score_result_id=result.id,
                    dimension_key=dimension.dimension_key,
                    dimension_name=dimension.name,
                    score=result.score,
                    assessment_status=result.assessment_status,
                    reason=result.reason,
                    confidence=float(result.confidence) if result.confidence is not None else None,
                    evidence_sufficiency_index=result.evidence_sufficiency_index,
                    score_kind=_persisted_score_kind(
                        session,
                        result,
                        dimension.dimension_key,
                    ),
                    scoring_source=result.scoring_source,
                    evidence=[
                        AdminReviewEvidence(
                            evidence_id=evidence.id,
                            dialogue_turn_id=evidence.dialogue_turn_id,
                            evidence_text=evidence.evidence_text,
                            evidence_type=evidence.evidence_type,
                            explanation=evidence.explanation,
                            created_at=evidence.created_at,
                        )
                        for evidence in evidence_by_result.get(result.id, [])
                    ],
                    created_at=result.created_at,
                )
            )

        report = self.db.execute(
            select(AssessmentReport).where(AssessmentReport.session_id == session.id)
        ).scalar_one_or_none()
        feedback = self.db.execute(
            select(SessionFeedback).where(SessionFeedback.session_id == session.id)
        ).scalar_one_or_none()
        human_review = self._human_review_out(session.id)
        expert_score_targets = self._expert_score_targets(session)
        expert_scores = self._expert_score_outs(
            session.id,
            current_annotator_id=current_annotator_id,
        )
        generation_job = self.db.execute(
            select(ScenarioGenerationJob).where(
                ScenarioGenerationJob.session_id == session.id
            )
        ).scalar_one_or_none()

        return AdminSessionReviewResponse(
            session=AdminReviewSession(
                session_uuid=session.session_uuid,
                nickname=participant.nickname,
                scenario_code=scenario.scenario_code,
                scenario_title=scenario.title,
                scenario_version=scenario.version,
                scenario_source_type=scenario.source_type,
                base_scenario_id=scenario.base_scenario_id,
                occupation_category=participant.industry,
                occupation=participant.career_direction,
                scenario_generation_status=(
                    generation_job.status if generation_job else None
                ),
                scenario_cache_hit=bool(generation_job and generation_job.cache_hit),
                scenario_fallback_used=bool(
                    generation_job and generation_job.fallback_used
                ),
                status=session.status,
                assessment_mode=session.assessment_mode,
                flow_version=session.flow_version,
                interviewer_style_version=session.interviewer_style_version,
                state_version=session.state_version,
                current_stage_code=current_stage.stage_code if current_stage else None,
                current_stage_title=current_stage.title if current_stage else None,
                started_at=session.started_at,
                completed_at=session.completed_at,
                duration_minutes=_duration_minutes(session.total_duration_seconds),
                created_at=session.created_at,
                updated_at=session.updated_at,
            ),
            turns=[
                AdminReviewTurn(
                    turn_id=turn.id,
                    turn_index=turn.turn_index,
                    stage_code=stage_map.get(turn.stage_id, (None, None))[0],
                    stage_title=stage_map.get(turn.stage_id, (None, None))[1],
                    speaker=turn.speaker,
                    content=turn.content,
                    content_type=turn.content_type,
                    source_agent_trace_id=turn.source_agent_trace_id,
                    intervention_rule_code=rule_map.get(turn.intervention_rule_id),
                    dynamic_info_code=info_map.get(turn.dynamic_info_id),
                    client_turn_id=turn.client_turn_id,
                    answer_duration_ms=turn.answer_duration_ms,
                    created_at=turn.created_at,
                )
                for turn in turns
            ],
            traces=[
                AdminReviewTrace(
                    trace_id=trace.id,
                    stage_code=stage_map.get(trace.stage_id, (None, None))[0],
                    stage_title=stage_map.get(trace.stage_id, (None, None))[1],
                    trigger_turn_id=trace.trigger_turn_id,
                    agent_name=trace.agent_name,
                    generation_mode=trace.generation_mode,
                    ai_generation_weight=trace.ai_generation_weight,
                    config_snapshot_json=trace.config_snapshot_json,
                    input_json=trace.input_json,
                    output_json=trace.output_json,
                    raw_output=trace.raw_output,
                    status=trace.status,
                    error_code=trace.error_code,
                    fallback_type=trace.fallback_type,
                    fallback_reason=trace_audit[trace.id]["fallback_reason"],
                    prompt_template_id=trace.prompt_template_id,
                    parent_trace_id=trace_audit[trace.id]["parent_trace_id"],
                    interviewer_style_version=trace_audit[trace.id][
                        "interviewer_style_version"
                    ],
                    validation_codes=trace_audit[trace.id]["validation_codes"],
                    model_name=trace.model_name,
                    duration_ms=trace.duration_ms,
                    selected_rule_code=rule_map.get(trace.selected_rule_id),
                    selected_dynamic_info_code=info_map.get(trace.selected_dynamic_info_id),
                    created_at=trace.created_at,
                )
                for trace in traces
            ],
            score_snapshots=[
                AdminReviewScoreSnapshot(
                    snapshot_id=snapshot.id,
                    stage_code=stage_map.get(snapshot.stage_id, (None, None))[0],
                    stage_title=stage_map.get(snapshot.stage_id, (None, None))[1],
                    dialogue_turn_id=snapshot.dialogue_turn_id,
                    snapshot_type=snapshot.snapshot_type,
                    summary=snapshot.summary,
                    trend_analysis=snapshot.trend_analysis,
                    agent_trace_id=snapshot.agent_trace_id,
                    results=results_by_snapshot.get(snapshot.id, []),
                    created_at=snapshot.created_at,
                )
                for snapshot in snapshots
            ],
            report=(
                AdminReviewReport(
                    status=report.status,
                    summary=report.summary,
                    report_json=report.report_json,
                    created_at=report.created_at,
                    updated_at=report.updated_at,
                )
                if report
                else None
            ),
            feedback=(
                AdminReviewFeedback(
                    realism_score=feedback.realism_score,
                    difficulty_score=feedback.difficulty_score,
                    naturalness_score=feedback.naturalness_score,
                    fatigue_score=feedback.fatigue_score,
                    report_trust_score=feedback.report_trust_score,
                    overall_satisfaction_score=feedback.overall_satisfaction_score,
                    open_feedback=feedback.open_feedback,
                    submitted_at=feedback.updated_at,
                )
                if feedback
                else None
            ),
            human_review=human_review,
            expert_score_targets=expert_score_targets,
            expert_scores=expert_scores,
            progressive_audit=(
                {
                    "blueprint": (scenario.generation_metadata_json or {}).get(
                        "interview_blueprint"
                    ),
                    "blueprint_fingerprint": (
                        scenario.generation_metadata_json or {}
                    ).get("interview_blueprint_fingerprint"),
                    "interview_state": session.interview_state_json,
                    "dimension_slots": (
                        (session.interview_state_json or {}).get("dimension_slots")
                    ),
                    "evidence_timeline": (
                        (session.interview_state_json or {}).get("evidence_timeline")
                    ),
                    "planner_decisions": [
                        trace.output_json
                        for trace in traces
                        if trace.agent_name in {"planner", "consultative_turn"}
                    ],
                    "consultative_turn_audit": [
                        {
                            "output": trace.output_json,
                            "config": trace.config_snapshot_json,
                            "status": trace.status,
                            "fallback_type": trace.fallback_type,
                            "model_name": trace.model_name,
                            "duration_ms": trace.duration_ms,
                        }
                        for trace in traces
                        if trace.agent_name == "consultative_turn"
                    ],
                    "released_events": (
                        (session.interview_state_json or {}).get(
                            "released_event_codes"
                        )
                    ),
                    "released_units": (
                        (session.interview_state_json or {}).get(
                            "released_unit_codes"
                        )
                    ),
                    "measurement_quality": EvidenceSufficiencyService(
                        self.db
                    ).measurement_quality(session).model_dump(mode="json"),
                }
                if session.flow_version in {
                    "progressive_v3", "progressive_v3_2", "progressive_v3_3"
                }
                else None
            ),
        )

    def save_human_review(
        self,
        session_uuid: str,
        *,
        reviewer: AdminUser,
        payload: HumanReviewUpdate,
    ) -> HumanReviewOut:
        session = self._session_or_404(session_uuid)
        review = self.db.execute(
            select(HumanReview).where(HumanReview.session_id == session.id)
        ).scalar_one_or_none()
        if review is None:
            review = HumanReview(
                session_id=session.id,
                reviewer_id=reviewer.id,
                status=payload.status,
            )
            self.db.add(review)
        review.reviewer_id = reviewer.id
        review.status = payload.status
        review.decision = payload.decision
        review.notes = payload.notes.strip() if payload.notes and payload.notes.strip() else None
        review.completed_at = (
            datetime.utcnow()
            if payload.status in {"completed", "needs_adjudication"}
            else None
        )
        self.db.commit()
        return self._human_review_out(session.id)

    def save_expert_scores(
        self,
        session_uuid: str,
        *,
        annotator: AdminUser,
        items: list[ExpertScoreWrite],
    ) -> ExpertScoreBatchResponse:
        session, resolved = self._resolve_expert_score_items(session_uuid, items)
        self._upsert_expert_scores(
            session.id,
            annotator_id=annotator.id,
            resolved=resolved,
            source="manual",
            import_batch_id=None,
        )
        self.db.commit()
        return ExpertScoreBatchResponse(
            saved_count=len(items),
            items=self._expert_score_outs(
                session.id,
                current_annotator_id=annotator.id,
                annotator_id=annotator.id,
            ),
        )

    def import_expert_scores(
        self,
        csv_content: bytes,
        *,
        annotator: AdminUser,
    ) -> ExpertScoreBatchResponse:
        parsed_rows = _parse_expert_score_csv(csv_content)
        grouped: dict[str, list[ExpertScoreWrite]] = defaultdict(list)
        seen: set[tuple[str, str, str]] = set()
        duplicate_errors: list[dict[str, Any]] = []
        for row_number, session_uuid, item in parsed_rows:
            key = (session_uuid, item.stage_code, item.dimension_key)
            if key in seen:
                duplicate_errors.append(
                    {
                        "row": row_number,
                        "message": (
                            "Duplicate session_uuid, stage_code, and dimension_key "
                            "for the current annotator"
                        ),
                    }
                )
            seen.add(key)
            grouped[session_uuid].append(item)
        if duplicate_errors:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "message": "CSV validation failed; no rows were imported",
                    "errors": duplicate_errors,
                },
            )

        resolved_groups: list[
            tuple[AssessmentSession, list[tuple[ExpertScoreWrite, int, int]]]
        ] = []
        validation_errors: list[dict[str, Any]] = []
        for session_uuid, items in grouped.items():
            try:
                resolved_groups.append(
                    self._resolve_expert_score_items(session_uuid, items)
                )
            except HTTPException as exc:
                validation_errors.append(
                    {"session_uuid": session_uuid, "message": exc.detail}
                )
        if validation_errors:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "message": "CSV validation failed; no rows were imported",
                    "errors": validation_errors,
                },
            )

        import_batch_id = str(uuid4())
        for session, resolved in resolved_groups:
            self._upsert_expert_scores(
                session.id,
                annotator_id=annotator.id,
                resolved=resolved,
                source="csv_import",
                import_batch_id=import_batch_id,
            )
        self.db.commit()

        saved_items: list[ExpertScoreOut] = []
        for session, _resolved in resolved_groups:
            saved_items.extend(
                self._expert_score_outs(
                    session.id,
                    current_annotator_id=annotator.id,
                    annotator_id=annotator.id,
                    import_batch_id=import_batch_id,
                )
            )
        return ExpertScoreBatchResponse(
            saved_count=len(parsed_rows),
            imported_count=len(parsed_rows),
            import_batch_id=import_batch_id,
            items=saved_items,
        )

    def build_export(
        self,
        *,
        status_value: str | None,
        scenario_code: str | None,
        search: str | None,
        review_status: str | None,
        low_confidence: bool,
        confidence_threshold: float,
    ) -> dict[str, Any]:
        rows = self._matching_sessions(
            status_value,
            scenario_code,
            search,
            review_status,
            low_confidence,
            confidence_threshold,
        )
        tables: dict[str, list[dict[str, Any]]] = {
            "sessions": [],
            "turns": [],
            "agent_traces": [],
            "score_snapshots": [],
            "score_results": [],
            "score_evidence": [],
            "reports": [],
            "feedback": [],
            "human_reviews": [],
            "expert_scores": [],
        }
        for session, participant, scenario in rows:
            self._append_export_session(tables, session, participant, scenario)

        generated_at = datetime.now(timezone.utc).isoformat()
        manifest = {
            "schema_version": "research_export_v2",
            "generated_at": generated_at,
            "filters": {
                "status": status_value,
                "scenario_code": scenario_code,
                "search_applied": bool(search),
                "review_status": review_status,
                "low_confidence": low_confidence,
                "confidence_threshold": confidence_threshold,
            },
            "record_counts": {name: len(records) for name, records in tables.items()},
            "deidentification": "Direct participant fields and source identifiers are removed or pseudonymized.",
            "privacy_notice": (
                "Free-text dialogue, model output, reports, and feedback may contain personal "
                "information voluntarily entered by participants. This is de-identified research "
                "data, not guaranteed anonymous data."
            ),
            "contains_free_text": True,
        }
        return {"manifest": manifest, **tables}

    @staticmethod
    def build_csv_zip(payload: dict[str, Any]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(payload["manifest"], ensure_ascii=False, indent=2).encode("utf-8"),
            )
            for name, rows in payload.items():
                if name == "manifest":
                    continue
                archive.writestr(f"{name}.csv", _csv_bytes(rows))
        return buffer.getvalue()

    def _append_export_session(
        self,
        tables: dict[str, list[dict[str, Any]]],
        session: AssessmentSession,
        participant: Participant,
        scenario: Scenario,
    ) -> None:
        settings = get_settings()
        secret = settings.EXPORT_PSEUDONYM_SECRET.encode("utf-8")
        anon_id = _opaque_id(secret, "session", session.session_uuid, prefix="anon")
        stage_map = self._stage_map(scenario.id)
        rule_map, info_map = self._rule_and_info_maps(stage_map)
        turns = self.db.execute(
            select(DialogueTurn)
            .where(DialogueTurn.session_id == session.id)
            .order_by(DialogueTurn.turn_index)
        ).scalars().all()
        turns = [turn for turn in turns if not turn.content_type.startswith("profile_")]
        traces = self.db.execute(
            select(AgentTrace)
            .where(AgentTrace.session_id == session.id)
            .order_by(AgentTrace.created_at, AgentTrace.id)
        ).scalars().all()
        traces = [trace for trace in traces if trace.agent_name != "profile"]
        snapshots = self.db.execute(
            select(ScoreSnapshot)
            .where(ScoreSnapshot.session_id == session.id)
            .order_by(ScoreSnapshot.created_at, ScoreSnapshot.id)
        ).scalars().all()
        reports = self.db.execute(
            select(AssessmentReport).where(AssessmentReport.session_id == session.id)
        ).scalars().all()
        feedback_items = self.db.execute(
            select(SessionFeedback).where(SessionFeedback.session_id == session.id)
        ).scalars().all()
        human_review_row = self.db.execute(
            select(HumanReview).where(HumanReview.session_id == session.id)
        ).scalar_one_or_none()
        expert_rows = self.db.execute(
            select(
                ExpertScoreAnnotation,
                ScenarioStage.stage_code,
                RubricDimension.dimension_key,
            )
            .join(ScenarioStage, ScenarioStage.id == ExpertScoreAnnotation.stage_id)
            .join(
                RubricDimension,
                RubricDimension.id == ExpertScoreAnnotation.dimension_id,
            )
            .where(ExpertScoreAnnotation.session_id == session.id)
            .order_by(
                ExpertScoreAnnotation.annotator_id,
                ScenarioStage.stage_order,
                RubricDimension.id,
            )
        ).all()
        ai_score_map = self._ai_score_map(session.id)

        turn_ids = {
            turn.id: _opaque_id(secret, "turn", f"{session.session_uuid}:{turn.id}", "turn")
            for turn in turns
        }
        trace_ids = {
            trace.id: _opaque_id(secret, "trace", f"{session.session_uuid}:{trace.id}", "trace")
            for trace in traces
        }
        snapshot_ids = {
            snapshot.id: _opaque_id(
                secret, "snapshot", f"{session.session_uuid}:{snapshot.id}", "snapshot"
            )
            for snapshot in snapshots
        }

        tables["sessions"].append(
            {
                "anonymous_session_id": anon_id,
                "scenario_code": scenario.scenario_code,
                "scenario_title": scenario.title,
                "scenario_version": scenario.version,
                "scenario_source_type": scenario.source_type,
                "occupation_category": participant.industry,
                "status": session.status,
                "assessment_mode": session.assessment_mode,
                "flow_version": session.flow_version,
                "interviewer_style_version": session.interviewer_style_version,
                "state_version": session.state_version,
                "measurement_quality": (
                    EvidenceSufficiencyService(self.db)
                    .measurement_quality(session)
                    .model_dump(mode="json")
                    if session.flow_version in {
                        "progressive_v3", "progressive_v3_2", "progressive_v3_3"
                    }
                    else None
                ),
                "started_at": _iso(session.started_at),
                "completed_at": _iso(session.completed_at),
                "total_duration_seconds": session.total_duration_seconds,
                "created_at": _iso(session.created_at),
                "updated_at": _iso(session.updated_at),
            }
        )
        for turn in turns:
            tables["turns"].append(
                {
                    "anonymous_session_id": anon_id,
                    "turn_id": turn_ids[turn.id],
                    "turn_index": turn.turn_index,
                    "stage_code": stage_map.get(turn.stage_id, (None, None))[0],
                    "speaker": turn.speaker,
                    "content": _scrub_text(turn.content, session.session_uuid, participant.nickname, anon_id),
                    "content_type": turn.content_type,
                    "client_turn_id": turn.client_turn_id,
                    "answer_duration_ms": turn.answer_duration_ms,
                    "source_agent_trace_id": trace_ids.get(turn.source_agent_trace_id),
                    "intervention_rule_code": rule_map.get(turn.intervention_rule_id),
                    "dynamic_info_code": info_map.get(turn.dynamic_info_id),
                    "created_at": _iso(turn.created_at),
                }
            )
        for trace in traces:
            is_preparation_trace = trace.agent_name in {
                "scenario_design",
                "scenario_review",
                "scenario_adaptation",
            }
            trace_audit = _trace_audit_fields(trace.config_snapshot_json)
            tables["agent_traces"].append(
                {
                    "anonymous_session_id": anon_id,
                    "trace_id": trace_ids[trace.id],
                    "stage_code": stage_map.get(trace.stage_id, (None, None))[0],
                    "trigger_turn_id": turn_ids.get(trace.trigger_turn_id),
                    "agent_name": trace.agent_name,
                    "generation_mode": trace.generation_mode,
                    "ai_generation_weight": trace.ai_generation_weight,
                    "config_snapshot_json": _sanitize_json(
                        trace.config_snapshot_json,
                        session.session_uuid,
                        participant.nickname,
                        anon_id,
                        turn_id_map=turn_ids,
                        trace_id_map=trace_ids,
                    ),
                    "input_json": (
                        {
                            "occupation_category": participant.industry,
                            "profile_redacted": True,
                        }
                        if is_preparation_trace
                        else _sanitize_json(
                            trace.input_json,
                            session.session_uuid,
                            participant.nickname,
                            anon_id,
                            turn_id_map=turn_ids,
                            trace_id_map=trace_ids,
                        )
                    ),
                    "output_json": (
                        None
                        if is_preparation_trace
                        else _sanitize_json(
                            trace.output_json,
                            session.session_uuid,
                            participant.nickname,
                            anon_id,
                            turn_id_map=turn_ids,
                            trace_id_map=trace_ids,
                        )
                    ),
                    "raw_output": (
                        None
                        if is_preparation_trace
                        else _scrub_text(
                            trace.raw_output,
                            session.session_uuid,
                            participant.nickname,
                            anon_id,
                        )
                    ),
                    "status": trace.status,
                    "error_code": trace.error_code,
                    "fallback_type": trace.fallback_type,
                    "fallback_reason": trace_audit["fallback_reason"],
                    "prompt_template_id": trace.prompt_template_id,
                    "parent_trace_id": trace_ids.get(trace_audit["parent_trace_id"]),
                    "interviewer_style_version": trace_audit[
                        "interviewer_style_version"
                    ],
                    "validation_codes": trace_audit["validation_codes"],
                    "model_name": trace.model_name,
                    "duration_ms": trace.duration_ms,
                    "selected_rule_code": rule_map.get(trace.selected_rule_id),
                    "selected_dynamic_info_code": info_map.get(trace.selected_dynamic_info_id),
                    "created_at": _iso(trace.created_at),
                }
            )

        result_id_map: dict[int, str] = {}
        for snapshot in snapshots:
            tables["score_snapshots"].append(
                {
                    "anonymous_session_id": anon_id,
                    "snapshot_id": snapshot_ids[snapshot.id],
                    "stage_code": stage_map.get(snapshot.stage_id, (None, None))[0],
                    "dialogue_turn_id": turn_ids.get(snapshot.dialogue_turn_id),
                    "snapshot_type": snapshot.snapshot_type,
                    "summary": _scrub_text(
                        snapshot.summary, session.session_uuid, participant.nickname, anon_id
                    ),
                    "trend_analysis": _scrub_text(
                        snapshot.trend_analysis,
                        session.session_uuid,
                        participant.nickname,
                        anon_id,
                    ),
                    "agent_trace_id": trace_ids.get(snapshot.agent_trace_id),
                    "created_at": _iso(snapshot.created_at),
                }
            )
            result_rows = self.db.execute(
                select(ScoreResult, RubricDimension)
                .join(RubricDimension, RubricDimension.id == ScoreResult.dimension_id)
                .where(ScoreResult.snapshot_id == snapshot.id)
                .order_by(ScoreResult.id)
            ).all()
            for result, dimension in result_rows:
                export_result_id = _opaque_id(
                    secret, "score_result", f"{session.session_uuid}:{result.id}", "result"
                )
                result_id_map[result.id] = export_result_id
                tables["score_results"].append(
                    {
                        "anonymous_session_id": anon_id,
                        "score_result_id": export_result_id,
                        "snapshot_id": snapshot_ids[snapshot.id],
                        "dimension_key": dimension.dimension_key,
                        "dimension_name": dimension.name,
                        "score": result.score,
                        "assessment_status": result.assessment_status,
                        "reason": _scrub_text(
                            result.reason, session.session_uuid, participant.nickname, anon_id
                        ),
                        "confidence": float(result.confidence)
                        if result.confidence is not None
                        else None,
                        "evidence_sufficiency_index": result.evidence_sufficiency_index,
                        "score_kind": _persisted_score_kind(
                            session,
                            result,
                            dimension.dimension_key,
                        ),
                        "scoring_source": result.scoring_source,
                        "created_at": _iso(result.created_at),
                    }
                )
        evidence_id_map: dict[int, str] = {}
        if result_id_map:
            evidence_items = self.db.execute(
                select(ScoreEvidence)
                .where(ScoreEvidence.score_result_id.in_(list(result_id_map)))
                .order_by(ScoreEvidence.id)
            ).scalars().all()
            for evidence in evidence_items:
                export_evidence_id = _opaque_id(
                    secret,
                    "score_evidence",
                    f"{session.session_uuid}:{evidence.id}",
                    "evidence",
                )
                evidence_id_map[evidence.id] = export_evidence_id
                tables["score_evidence"].append(
                    {
                        "anonymous_session_id": anon_id,
                        "evidence_id": export_evidence_id,
                        "score_result_id": result_id_map[evidence.score_result_id],
                        "dialogue_turn_id": turn_ids.get(evidence.dialogue_turn_id),
                        "evidence_text": _scrub_text(
                            evidence.evidence_text,
                            session.session_uuid,
                            participant.nickname,
                            anon_id,
                        ),
                        "evidence_type": evidence.evidence_type,
                        "explanation": _scrub_text(
                            evidence.explanation,
                            session.session_uuid,
                            participant.nickname,
                            anon_id,
                        ),
                        "created_at": _iso(evidence.created_at),
                    }
                )
        if human_review_row is not None:
            tables["human_reviews"].append(
                {
                    "anonymous_session_id": anon_id,
                    "review_status": human_review_row.status,
                    "review_decision": human_review_row.decision,
                    "review_notes": _scrub_text(
                        human_review_row.notes,
                        session.session_uuid,
                        participant.nickname,
                        anon_id,
                    ),
                    "reviewer_id": _opaque_id(
                        secret,
                        "reviewer",
                        str(human_review_row.reviewer_id),
                        "expert",
                    ),
                    "completed_at": _iso(human_review_row.completed_at),
                    "created_at": _iso(human_review_row.created_at),
                    "updated_at": _iso(human_review_row.updated_at),
                }
            )
        for annotation, stage_code, dimension_key in expert_rows:
            ai_score, ai_confidence = ai_score_map.get(
                (annotation.stage_id, annotation.dimension_id),
                (None, None),
            )
            tables["expert_scores"].append(
                {
                    "anonymous_session_id": anon_id,
                    "expert_score_id": _opaque_id(
                        secret,
                        "expert_score",
                        f"{session.session_uuid}:{annotation.id}",
                        "expert_score",
                    ),
                    "stage_code": stage_code,
                    "dimension_key": dimension_key,
                    "annotator_id": _opaque_id(
                        secret,
                        "annotator",
                        str(annotation.annotator_id),
                        "expert",
                    ),
                    "assessment_status": annotation.assessment_status,
                    "score": annotation.score,
                    "evidence_ids": [
                        evidence_id_map[evidence_id]
                        for evidence_id in (annotation.evidence_ids_json or [])
                        if evidence_id in evidence_id_map
                    ],
                    "bars_reason": _scrub_text(
                        annotation.bars_reason,
                        session.session_uuid,
                        participant.nickname,
                        anon_id,
                    ),
                    "next_level_gap": _scrub_text(
                        annotation.next_level_gap,
                        session.session_uuid,
                        participant.nickname,
                        anon_id,
                    ),
                    "annotator_confidence": annotation.annotator_confidence,
                    "review_flag": annotation.review_flag,
                    "review_reason": _scrub_text(
                        annotation.review_reason,
                        session.session_uuid,
                        participant.nickname,
                        anon_id,
                    ),
                    "source": annotation.source,
                    "import_batch_id": annotation.import_batch_id,
                    "ai_score": ai_score,
                    "ai_confidence": ai_confidence,
                    "score_difference": (
                        annotation.score - ai_score
                        if annotation.score is not None and ai_score is not None
                        else None
                    ),
                    "created_at": _iso(annotation.created_at),
                    "updated_at": _iso(annotation.updated_at),
                }
            )
        for report in reports:
            tables["reports"].append(
                {
                    "anonymous_session_id": anon_id,
                    "report_id": _opaque_id(
                        secret, "report", f"{session.session_uuid}:{report.id}", "report"
                    ),
                    "status": report.status,
                    "summary": _scrub_text(
                        report.summary, session.session_uuid, participant.nickname, anon_id
                    ),
                    "report_json": _sanitize_json(
                        report.report_json,
                        session.session_uuid,
                        participant.nickname,
                        anon_id,
                    ),
                    "created_at": _iso(report.created_at),
                    "updated_at": _iso(report.updated_at),
                }
            )
        for feedback in feedback_items:
            tables["feedback"].append(
                {
                    "anonymous_session_id": anon_id,
                    "feedback_id": _opaque_id(
                        secret,
                        "feedback",
                        f"{session.session_uuid}:{feedback.id}",
                        "feedback",
                    ),
                    "realism_score": feedback.realism_score,
                    "difficulty_score": feedback.difficulty_score,
                    "naturalness_score": feedback.naturalness_score,
                    "fatigue_score": feedback.fatigue_score,
                    "report_trust_score": feedback.report_trust_score,
                    "overall_satisfaction_score": feedback.overall_satisfaction_score,
                    "open_feedback": _scrub_text(
                        feedback.open_feedback,
                        session.session_uuid,
                        participant.nickname,
                        anon_id,
                    ),
                    "submitted_at": _iso(feedback.updated_at),
                }
            )
        if participant.career_direction:
            for rows in tables.values():
                for row in rows:
                    _redact_specific_occupation(row, participant.career_direction)

    def _session_or_404(self, session_uuid: str) -> AssessmentSession:
        session = self.db.execute(
            select(AssessmentSession).where(
                AssessmentSession.session_uuid == session_uuid
            )
        ).scalar_one_or_none()
        if session is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Assessment session not found",
            )
        return session

    def _human_review_out(self, session_id: int) -> HumanReviewOut:
        row = self.db.execute(
            select(HumanReview, AdminUser)
            .join(AdminUser, AdminUser.id == HumanReview.reviewer_id)
            .where(HumanReview.session_id == session_id)
        ).one_or_none()
        if row is None:
            return HumanReviewOut(status="pending")
        review, reviewer = row
        return HumanReviewOut(
            status=review.status,
            decision=review.decision,
            notes=review.notes,
            reviewer_id=review.reviewer_id,
            reviewer_name=reviewer.display_name,
            completed_at=review.completed_at,
            created_at=review.created_at,
            updated_at=review.updated_at,
        )

    def _expert_score_targets(
        self,
        session: AssessmentSession,
    ) -> list[ExpertScoreTarget]:
        rows = self.db.execute(
            select(ScenarioStage, RubricDimension)
            .join(
                ScenarioStageDimension,
                ScenarioStageDimension.stage_id == ScenarioStage.id,
            )
            .join(
                RubricDimension,
                RubricDimension.id == ScenarioStageDimension.dimension_id,
            )
            .where(ScenarioStage.scenario_id == session.scenario_id)
            .order_by(ScenarioStage.stage_order, RubricDimension.id)
        ).all()
        ai_score_map = self._ai_score_map(session.id)
        return [
            ExpertScoreTarget(
                stage_code=stage.stage_code,
                stage_title=stage.title,
                dimension_key=dimension.dimension_key,
                dimension_name=dimension.name,
                ai_score=ai_score_map.get(
                    (stage.id, dimension.id),
                    (None, None),
                )[0],
                ai_confidence=ai_score_map.get(
                    (stage.id, dimension.id),
                    (None, None),
                )[1],
            )
            for stage, dimension in rows
        ]

    def _ai_score_map(
        self,
        session_id: int,
    ) -> dict[tuple[int | None, int], tuple[int | None, float | None]]:
        rows = self.db.execute(
            select(
                ScoreSnapshot.stage_id,
                ScoreResult.dimension_id,
                ScoreResult.score,
                ScoreResult.confidence,
            )
            .join(ScoreResult, ScoreResult.snapshot_id == ScoreSnapshot.id)
            .where(ScoreSnapshot.session_id == session_id)
            .order_by(ScoreSnapshot.id, ScoreResult.id)
        ).all()
        result: dict[
            tuple[int | None, int],
            tuple[int | None, float | None],
        ] = {}
        for stage_id, dimension_id, score, confidence in rows:
            result[(stage_id, dimension_id)] = (
                score,
                float(confidence) if confidence is not None else None,
            )
        return result

    def _expert_score_outs(
        self,
        session_id: int,
        *,
        current_annotator_id: int,
        annotator_id: int | None = None,
        import_batch_id: str | None = None,
    ) -> list[ExpertScoreOut]:
        statement = (
            select(
                ExpertScoreAnnotation,
                ScenarioStage,
                RubricDimension,
                AdminUser,
            )
            .join(ScenarioStage, ScenarioStage.id == ExpertScoreAnnotation.stage_id)
            .join(
                RubricDimension,
                RubricDimension.id == ExpertScoreAnnotation.dimension_id,
            )
            .join(AdminUser, AdminUser.id == ExpertScoreAnnotation.annotator_id)
            .where(ExpertScoreAnnotation.session_id == session_id)
        )
        if annotator_id is not None:
            statement = statement.where(
                ExpertScoreAnnotation.annotator_id == annotator_id
            )
        if import_batch_id is not None:
            statement = statement.where(
                ExpertScoreAnnotation.import_batch_id == import_batch_id
            )
        rows = self.db.execute(
            statement.order_by(
                ScenarioStage.stage_order,
                RubricDimension.id,
                ExpertScoreAnnotation.annotator_id,
            )
        ).all()
        ai_score_map = self._ai_score_map(session_id)
        output: list[ExpertScoreOut] = []
        for annotation, stage, dimension, annotator in rows:
            ai_score, ai_confidence = ai_score_map.get(
                (stage.id, dimension.id),
                (None, None),
            )
            output.append(
                ExpertScoreOut(
                    annotation_id=annotation.id,
                    stage_code=stage.stage_code,
                    stage_title=stage.title,
                    dimension_key=dimension.dimension_key,
                    dimension_name=dimension.name,
                    annotator_id=annotation.annotator_id,
                    annotator_name=annotator.display_name,
                    is_current_annotator=(
                        annotation.annotator_id == current_annotator_id
                    ),
                    assessment_status=annotation.assessment_status,
                    score=annotation.score,
                    evidence_ids=annotation.evidence_ids_json or [],
                    bars_reason=annotation.bars_reason,
                    next_level_gap=annotation.next_level_gap,
                    annotator_confidence=annotation.annotator_confidence,
                    review_flag=annotation.review_flag,
                    review_reason=annotation.review_reason,
                    source=annotation.source,
                    import_batch_id=annotation.import_batch_id,
                    ai_score=ai_score,
                    ai_confidence=ai_confidence,
                    score_difference=(
                        annotation.score - ai_score
                        if annotation.score is not None and ai_score is not None
                        else None
                    ),
                    created_at=annotation.created_at,
                    updated_at=annotation.updated_at,
                )
            )
        return output

    def _resolve_expert_score_items(
        self,
        session_uuid: str,
        items: list[ExpertScoreWrite],
    ) -> tuple[
        AssessmentSession,
        list[tuple[ExpertScoreWrite, int, int]],
    ]:
        session = self._session_or_404(session_uuid)
        target_rows = self.db.execute(
            select(
                ScenarioStage.stage_code,
                ScenarioStage.id,
                RubricDimension.dimension_key,
                RubricDimension.id,
            )
            .join(
                ScenarioStageDimension,
                ScenarioStageDimension.stage_id == ScenarioStage.id,
            )
            .join(
                RubricDimension,
                RubricDimension.id == ScenarioStageDimension.dimension_id,
            )
            .where(ScenarioStage.scenario_id == session.scenario_id)
        ).all()
        target_map = {
            (stage_code, dimension_key): (stage_id, dimension_id)
            for stage_code, stage_id, dimension_key, dimension_id in target_rows
        }
        resolved: list[tuple[ExpertScoreWrite, int, int]] = []
        seen: set[tuple[str, str]] = set()
        errors: list[str] = []
        for item in items:
            key = (item.stage_code, item.dimension_key)
            if key in seen:
                errors.append(
                    f"Duplicate target {item.stage_code}/{item.dimension_key}"
                )
                continue
            seen.add(key)
            target = target_map.get(key)
            if target is None:
                errors.append(
                    f"Unknown scenario target {item.stage_code}/{item.dimension_key}"
                )
                continue
            resolved.append((item, target[0], target[1]))

        evidence_ids = {
            evidence_id
            for item in items
            for evidence_id in item.evidence_ids
        }
        if evidence_ids:
            valid_evidence_ids = set(
                self.db.execute(
                    select(ScoreEvidence.id)
                    .join(
                        ScoreResult,
                        ScoreResult.id == ScoreEvidence.score_result_id,
                    )
                    .join(
                        ScoreSnapshot,
                        ScoreSnapshot.id == ScoreResult.snapshot_id,
                    )
                    .where(
                        ScoreSnapshot.session_id == session.id,
                        ScoreEvidence.id.in_(evidence_ids),
                    )
                ).scalars()
            )
            missing_ids = sorted(evidence_ids - valid_evidence_ids)
            if missing_ids:
                errors.append(
                    "Evidence IDs do not belong to this session: "
                    + ", ".join(str(item) for item in missing_ids)
                )
        if errors:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=errors,
            )
        return session, resolved

    def _upsert_expert_scores(
        self,
        session_id: int,
        *,
        annotator_id: int,
        resolved: list[tuple[ExpertScoreWrite, int, int]],
        source: str,
        import_batch_id: str | None,
    ) -> None:
        target_keys = {(stage_id, dimension_id) for _item, stage_id, dimension_id in resolved}
        existing_rows = self.db.execute(
            select(ExpertScoreAnnotation).where(
                ExpertScoreAnnotation.session_id == session_id,
                ExpertScoreAnnotation.annotator_id == annotator_id,
            )
        ).scalars().all()
        existing_map = {
            (row.stage_id, row.dimension_id): row
            for row in existing_rows
            if (row.stage_id, row.dimension_id) in target_keys
        }
        for item, stage_id, dimension_id in resolved:
            annotation = existing_map.get((stage_id, dimension_id))
            if annotation is None:
                annotation = ExpertScoreAnnotation(
                    session_id=session_id,
                    stage_id=stage_id,
                    dimension_id=dimension_id,
                    annotator_id=annotator_id,
                    assessment_status=item.assessment_status,
                    bars_reason=item.bars_reason,
                    annotator_confidence=item.annotator_confidence,
                )
                self.db.add(annotation)
            annotation.assessment_status = item.assessment_status
            annotation.score = item.score
            annotation.evidence_ids_json = item.evidence_ids or None
            annotation.bars_reason = item.bars_reason.strip()
            annotation.next_level_gap = (
                item.next_level_gap.strip()
                if item.next_level_gap and item.next_level_gap.strip()
                else None
            )
            annotation.annotator_confidence = item.annotator_confidence
            annotation.review_flag = item.review_flag
            annotation.review_reason = (
                item.review_reason.strip()
                if item.review_reason and item.review_reason.strip()
                else None
            )
            annotation.source = source
            annotation.import_batch_id = import_batch_id

    def _matching_sessions(
        self,
        status_value: str | None,
        scenario_code: str | None,
        search: str | None,
        review_status: str | None,
        low_confidence: bool,
        confidence_threshold: float,
    ) -> list[tuple[AssessmentSession, Participant, Scenario]]:
        conditions = self._session_conditions(
            status_value,
            scenario_code,
            search,
            review_status,
            low_confidence,
            confidence_threshold,
        )
        return list(
            self.db.execute(
                select(AssessmentSession, Participant, Scenario)
                .join(Participant, Participant.id == AssessmentSession.participant_id)
                .join(Scenario, Scenario.id == AssessmentSession.scenario_id)
                .outerjoin(HumanReview, HumanReview.session_id == AssessmentSession.id)
                .where(*conditions)
                .order_by(AssessmentSession.updated_at.desc(), AssessmentSession.id.desc())
            ).all()
        )

    @staticmethod
    def _session_conditions(
        status_value: str | None,
        scenario_code: str | None,
        search: str | None,
        review_status: str | None,
        low_confidence: bool,
        confidence_threshold: float,
    ) -> list[Any]:
        conditions: list[Any] = []
        if status_value:
            conditions.append(AssessmentSession.status == status_value)
        if scenario_code:
            conditions.append(Scenario.scenario_code == scenario_code)
        if search and search.strip():
            pattern = f"%{search.strip()}%"
            conditions.append(
                or_(
                    AssessmentSession.session_uuid.like(pattern),
                    Participant.nickname.like(pattern),
                )
            )
        if review_status == "pending":
            conditions.append(
                or_(HumanReview.id.is_(None), HumanReview.status == "pending")
            )
        elif review_status:
            conditions.append(HumanReview.status == review_status)
        if low_confidence:
            latest_final_snapshot_id = (
                select(func.max(ScoreSnapshot.id))
                .where(
                    ScoreSnapshot.session_id == AssessmentSession.id,
                    ScoreSnapshot.snapshot_type == "final",
                )
                .correlate(AssessmentSession)
                .scalar_subquery()
            )
            min_ai_confidence = (
                select(func.min(ScoreResult.confidence))
                .where(ScoreResult.snapshot_id == latest_final_snapshot_id)
                .correlate(AssessmentSession)
                .scalar_subquery()
            )
            conditions.append(min_ai_confidence < confidence_threshold)
        return conditions

    def _stage_map(self, scenario_id: int) -> dict[int | None, tuple[str | None, str | None]]:
        rows = self.db.execute(
            select(ScenarioStage.id, ScenarioStage.stage_code, ScenarioStage.title).where(
                ScenarioStage.scenario_id == scenario_id
            )
        ).all()
        return {stage_id: (stage_code, title) for stage_id, stage_code, title in rows}

    def _rule_and_info_maps(
        self,
        stage_map: dict[int | None, tuple[str | None, str | None]],
    ) -> tuple[dict[int | None, str | None], dict[int | None, str | None]]:
        stage_ids = [stage_id for stage_id in stage_map if stage_id is not None]
        if not stage_ids:
            return {}, {}
        rule_rows = self.db.execute(
            select(StageInterventionRule.id, StageInterventionRule.rule_code).where(
                StageInterventionRule.stage_id.in_(stage_ids)
            )
        ).all()
        info_rows = self.db.execute(
            select(StageDynamicInfo.id, StageDynamicInfo.info_code).where(
                StageDynamicInfo.stage_id.in_(stage_ids)
            )
        ).all()
        return (
            {rule_id: rule_code for rule_id, rule_code in rule_rows},
            {info_id: info_code for info_id, info_code in info_rows},
        )


def _duration_minutes(seconds: int | None) -> float | None:
    return round(seconds / 60, 1) if seconds is not None else None


def _persisted_score_kind(
    session: AssessmentSession,
    result: ScoreResult,
    dimension_key: str,
) -> str:
    state = session.interview_state_json or {}
    if dimension_key in (state.get("dimension_slots") or {}):
        return EvidenceSufficiencyService.dimension_result(
            state,
            dimension_key,
        ).score_kind
    # Historical/non-v3.3 records lack the ESI state needed to distinguish a
    # provisional result from an unobserved one. Preserve their old inference.
    return "supported" if result.score is not None else "unobserved"


def _opaque_id(secret: bytes, kind: str, value: str, prefix: str) -> str:
    digest = hmac.new(secret, f"{kind}:{value}".encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{prefix}_{digest[:16]}"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _trace_audit_fields(config_snapshot: Any) -> dict[str, Any]:
    config = config_snapshot if isinstance(config_snapshot, dict) else {}
    raw_codes = config.get("validation_codes", config.get("validation_errors", []))
    if isinstance(raw_codes, str):
        candidates = [raw_codes]
    elif isinstance(raw_codes, list):
        candidates = [str(code) for code in raw_codes if code]
    else:
        candidates = []
    validation_codes = []
    for code in candidates:
        if code in TRACE_VALIDATION_CODE_ALLOWLIST:
            validation_codes.append(code)
            continue
        if not code.startswith(TRACE_VALIDATION_CODE_PREFIXES):
            continue
        suffix = code.split(":", 1)[1]
        if (
            suffix
            and len(suffix) <= 48
            and all(character.isalnum() or character in "_-." for character in suffix)
        ):
            validation_codes.append(code)
    parent_trace_id = config.get("parent_trace_id")
    if not isinstance(parent_trace_id, int):
        parent_trace_id = None
    raw_fallback_reason = config.get("fallback_reason")
    fallback_reason = (
        raw_fallback_reason
        if isinstance(raw_fallback_reason, str)
        and len(raw_fallback_reason) <= 80
        and raw_fallback_reason == raw_fallback_reason.upper()
        and all(
            character.isalnum() or character == "_"
            for character in raw_fallback_reason
        )
        else None
    )
    raw_style = config.get("interviewer_style_version")
    interviewer_style_version = (
        raw_style
        if isinstance(raw_style, str)
        and raw_style in {
            "baseline_v1",
            "humanistic_v1",
            "humanistic_v1_1",
        }
        else None
    )
    return {
        "fallback_reason": fallback_reason,
        "parent_trace_id": parent_trace_id,
        "interviewer_style_version": interviewer_style_version,
        "validation_codes": validation_codes,
    }


def _scrub_text(
    value: str | None,
    session_uuid: str,
    nickname: str,
    anonymous_session_id: str,
) -> str | None:
    if value is None:
        return None
    cleaned = value.replace(session_uuid, anonymous_session_id)
    if nickname.strip():
        cleaned = cleaned.replace(nickname, "[已去标识]")
    return cleaned


_OMITTED_IDENTITY_KEYS = {
    "participant",
    "participant_id",
    "participant_profile",
    "nickname",
    "full_name",
    "display_name",
    "username",
    "email",
    "phone",
    "basic_info",
    "background_answers",
    "profile_summary",
    "raw_basic_info",
    "raw_background_answers",
    "ai_profile_json",
    "self_description",
    "admin",
    "created_by",
    "admin_user_id",
}


def _sanitize_json(
    value: Any,
    session_uuid: str,
    nickname: str,
    anonymous_session_id: str,
    *,
    turn_id_map: dict[int, str] | None = None,
    trace_id_map: dict[int, str] | None = None,
) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lowered = key.lower()
            if lowered in _OMITTED_IDENTITY_KEYS:
                continue
            if lowered in {"session_id", "session_uuid"}:
                result[key] = anonymous_session_id
                continue
            reference_map = _semantic_reference_map(
                lowered,
                turn_id_map=turn_id_map,
                trace_id_map=trace_id_map,
            )
            if reference_map is not None:
                result[key] = _pseudonymize_reference_value(item, reference_map)
                continue
            result[key] = _sanitize_json(
                item,
                session_uuid,
                nickname,
                anonymous_session_id,
                turn_id_map=turn_id_map,
                trace_id_map=trace_id_map,
            )
        return result
    if isinstance(value, list):
        return [
            _sanitize_json(
                item,
                session_uuid,
                nickname,
                anonymous_session_id,
                turn_id_map=turn_id_map,
                trace_id_map=trace_id_map,
            )
            for item in value
        ]
    if isinstance(value, str):
        return _scrub_text(value, session_uuid, nickname, anonymous_session_id)
    if isinstance(value, Decimal):
        return float(value)
    return value


def _semantic_reference_map(
    key: str,
    *,
    turn_id_map: dict[int, str] | None,
    trace_id_map: dict[int, str] | None,
) -> dict[int, str] | None:
    """Return the opaque-ID map for a semantic trace or dialogue pointer."""

    if key in {"trace_id", "trace_ids"} or key.endswith(
        ("_trace_id", "_trace_ids")
    ):
        return trace_id_map
    if key in {"turn_id", "turn_ids"} or key.endswith(
        ("_turn_id", "_turn_ids")
    ):
        return turn_id_map
    return None


def _pseudonymize_reference_value(
    value: Any,
    reference_map: dict[int, str],
) -> Any:
    """Pseudonymize semantic ID values without leaking unresolved DB integers."""

    if value is None:
        return None
    if isinstance(value, list):
        return [
            _pseudonymize_reference_value(item, reference_map)
            for item in value
        ]
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return reference_map.get(value)
    if isinstance(value, str):
        if value in reference_map.values():
            return value
        if value.isdecimal():
            return reference_map.get(int(value))
    return None


def _redact_specific_occupation(value: Any, occupation: str) -> Any:
    """Remove an exact free-text occupation while preserving the allowed category."""

    if isinstance(value, dict):
        for key, item in value.items():
            if key == "occupation_category":
                continue
            value[key] = _redact_specific_occupation(item, occupation)
        return value
    if isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = _redact_specific_occupation(item, occupation)
        return value
    if isinstance(value, str) and occupation.strip():
        return value.replace(occupation, "[具体职业已泛化]")
    return value


def _csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    if not rows:
        return "".encode("utf-8-sig")
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_value(row.get(key)) for key in fieldnames})
    return stream.getvalue().encode("utf-8-sig")


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def _parse_expert_score_csv(
    csv_content: bytes,
) -> list[tuple[int, str, ExpertScoreWrite]]:
    try:
        text = csv_content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": "CSV must use UTF-8 encoding",
                "errors": [{"message": str(exc)}],
            },
        ) from exc
    if not text.strip():
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": "CSV is empty", "errors": []},
        )

    reader = csv.DictReader(io.StringIO(text))
    required_columns = {
        "session_uuid",
        "stage_code",
        "dimension_key",
        "assessment_status",
        "score",
        "bars_reason",
        "annotator_confidence",
    }
    fieldnames = set(reader.fieldnames or [])
    missing_columns = sorted(required_columns - fieldnames)
    if missing_columns:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": "CSV is missing required columns",
                "errors": [{"missing_columns": missing_columns}],
            },
        )

    parsed: list[tuple[int, str, ExpertScoreWrite]] = []
    errors: list[dict[str, Any]] = []
    for row_number, row in enumerate(reader, start=2):
        session_uuid = (row.get("session_uuid") or "").strip()
        raw_status = (row.get("assessment_status") or "").strip().lower()
        assessment_status = (
            "insufficient_evidence"
            if raw_status in {"ie", "insufficient_evidence"}
            else raw_status
        )
        raw_score = (row.get("score") or "").strip()
        raw_review_flag = (row.get("review_flag") or "").strip().lower()
        try:
            review_flag = _parse_csv_bool(raw_review_flag)
            evidence_ids = _parse_csv_evidence_ids(row.get("evidence_ids") or "")
            payload = ExpertScoreWrite.model_validate(
                {
                    "stage_code": (row.get("stage_code") or "").strip(),
                    "dimension_key": (row.get("dimension_key") or "").strip(),
                    "assessment_status": assessment_status,
                    "score": int(raw_score) if raw_score else None,
                    "evidence_ids": evidence_ids,
                    "bars_reason": (row.get("bars_reason") or "").strip(),
                    "next_level_gap": (row.get("next_level_gap") or "").strip()
                    or None,
                    "annotator_confidence": (
                        row.get("annotator_confidence") or ""
                    ).strip().lower(),
                    "review_flag": review_flag,
                    "review_reason": (row.get("review_reason") or "").strip()
                    or None,
                }
            )
            if not session_uuid:
                raise ValueError("session_uuid is required")
            parsed.append((row_number, session_uuid, payload))
        except (ValueError, ValidationError) as exc:
            errors.append({"row": row_number, "message": str(exc)})

    if errors:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": "CSV validation failed; no rows were imported",
                "errors": errors,
            },
        )
    if not parsed:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": "CSV has no data rows", "errors": []},
        )
    return parsed


def _parse_csv_bool(value: str) -> bool:
    if value in {"", "0", "false", "no", "否"}:
        return False
    if value in {"1", "true", "yes", "是"}:
        return True
    raise ValueError(f"Invalid review_flag: {value}")


def _parse_csv_evidence_ids(value: str) -> list[int]:
    cleaned = value.strip()
    if not cleaned:
        return []
    if cleaned.startswith("["):
        parsed = json.loads(cleaned)
        if not isinstance(parsed, list):
            raise ValueError("evidence_ids JSON must be an array")
        return [int(item) for item in parsed]
    for separator in ("|", ";"):
        cleaned = cleaned.replace(separator, ",")
    return [int(item.strip()) for item in cleaned.split(",") if item.strip()]


__all__ = ["AdminSessionReviewService"]

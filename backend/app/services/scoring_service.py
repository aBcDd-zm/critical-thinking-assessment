from __future__ import annotations

from decimal import Decimal
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.schemas import AgentRuntimeContext, ScoringOutput
from app.core.config import get_settings
from app.models.agent import AgentTrace
from app.models.rubric import RubricDimension
from app.models.scoring import ScoreEvidence, ScoreResult, ScoreSnapshot


class ScoringService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def persist_scoring_failure(
        self,
        context: AgentRuntimeContext,
        *,
        error_code: str,
        reason: str,
        raw_output: str | None = None,
        generation_duration_ms: int = 0,
    ) -> AgentTrace:
        started_at = perf_counter()
        settings = get_settings()
        generation_mode = settings.MODEL_GATEWAY_MODE.lower()
        trace = AgentTrace(
            session_id=_require_id(context.session.session_id, "session_id"),
            stage_id=context.stage.stage_id,
            trigger_turn_id=(
                context.latest_user_turn.turn_id if context.latest_user_turn else None
            ),
            agent_name="scoring",
            generation_mode=generation_mode,
            ai_generation_weight=0,
            config_snapshot_json={
                "failure_reason": reason,
                "generation_duration_ms": max(generation_duration_ms, 0),
            },
            input_json=context.model_dump(mode="json"),
            output_json=None,
            raw_output=raw_output,
            status="failed",
            error_code=error_code,
            model_name=settings.DEEPSEEK_MODEL if generation_mode == "real" else "mock",
            duration_ms=max(generation_duration_ms, 0)
            + int((perf_counter() - started_at) * 1000),
        )
        self.db.add(trace)
        self.db.flush()
        return trace

    def persist_scoring_output(
        self,
        context: AgentRuntimeContext,
        output: ScoringOutput,
        *,
        generation_duration_ms: int = 0,
    ) -> ScoreSnapshot:
        started_at = perf_counter()
        settings = get_settings()
        generation_mode = settings.MODEL_GATEWAY_MODE.lower()
        trace = AgentTrace(
            session_id=_require_id(context.session.session_id, "session_id"),
            stage_id=context.stage.stage_id,
            trigger_turn_id=(
                context.latest_user_turn.turn_id if context.latest_user_turn else None
            ),
            agent_name="scoring",
            generation_mode=generation_mode,
            ai_generation_weight=0,
            config_snapshot_json={
                "snapshot_type": output.snapshot_type,
                "fallback_used": output.fallback_used,
                "warnings": output.warnings,
                "generation_duration_ms": max(generation_duration_ms, 0),
            },
            input_json=context.model_dump(mode="json"),
            output_json=output.model_dump(mode="json"),
            raw_output=None,
            status="ok",
            error_code=None,
            model_name=settings.DEEPSEEK_MODEL if generation_mode == "real" else "mock",
            duration_ms=max(generation_duration_ms, 0)
            + int((perf_counter() - started_at) * 1000),
        )
        self.db.add(trace)
        self.db.flush()

        snapshot = ScoreSnapshot(
            session_id=_require_id(context.session.session_id, "session_id"),
            stage_id=context.stage.stage_id,
            dialogue_turn_id=(
                context.latest_user_turn.turn_id if context.latest_user_turn else None
            ),
            snapshot_type=output.snapshot_type,
            summary=output.summary,
            trend_analysis=output.trend_analysis,
            agent_trace_id=trace.id,
        )
        self.db.add(snapshot)
        self.db.flush()

        dimensions = self._dimension_id_by_key()
        for item in output.scores:
            dimension_id = dimensions.get(item.dimension_key)
            if dimension_id is None:
                raise ValueError(f"Unknown rubric dimension: {item.dimension_key}")
            result = ScoreResult(
                snapshot_id=snapshot.id,
                dimension_id=dimension_id,
                score=item.score,
                assessment_status=item.assessment_status,
                reason=item.reason,
                confidence=(
                    Decimal(str(round(item.confidence, 3)))
                    if item.confidence is not None
                    else None
                ),
                evidence_sufficiency_index=item.evidence_sufficiency_index,
                scoring_source=item.scoring_source,
            )
            self.db.add(result)
            self.db.flush()
            for evidence in item.evidence:
                self.db.add(
                    ScoreEvidence(
                        score_result_id=result.id,
                        dialogue_turn_id=evidence.dialogue_turn_id,
                        evidence_text=evidence.text,
                        evidence_type=evidence.evidence_type,
                        explanation=evidence.explanation,
                    )
                )
        self.db.flush()
        return snapshot

    def _dimension_id_by_key(self) -> dict[str, int]:
        rows = self.db.execute(
            select(RubricDimension.dimension_key, RubricDimension.id).where(
                RubricDimension.status == "active"
            )
        ).all()
        return {dimension_key: dimension_id for dimension_key, dimension_id in rows}


def _require_id(value: int | None, name: str) -> int:
    if value is None:
        raise ValueError(f"context.session.{name} is required for persistence")
    return value

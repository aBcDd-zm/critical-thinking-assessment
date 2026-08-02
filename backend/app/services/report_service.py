from __future__ import annotations

from time import perf_counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.schemas import AgentRuntimeContext, ReportOutput
from app.core.config import get_settings
from app.models.agent import AgentTrace
from app.models.report import AssessmentReport


class ReportService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def persist_report_failure(
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
            agent_name="report",
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

    def persist_report_output(
        self,
        context: AgentRuntimeContext,
        output: ReportOutput,
        *,
        generation_duration_ms: int = 0,
    ) -> AssessmentReport:
        started_at = perf_counter()
        settings = get_settings()
        generation_mode = settings.MODEL_GATEWAY_MODE.lower()
        session_id = _require_id(context.session.session_id, "session_id")
        trace = AgentTrace(
            session_id=session_id,
            stage_id=context.stage.stage_id,
            trigger_turn_id=(
                context.latest_user_turn.turn_id if context.latest_user_turn else None
            ),
            agent_name="report",
            generation_mode=generation_mode,
            ai_generation_weight=0,
            config_snapshot_json={
                "report_version": "v2",
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

        report_json = output.model_dump(mode="json")
        report = self.db.execute(
            select(AssessmentReport).where(AssessmentReport.session_id == session_id)
        ).scalar_one_or_none()
        if report is None:
            report = AssessmentReport(
                session_id=session_id,
                report_template_id=None,
                agent_trace_id=trace.id,
                report_json=report_json,
                summary=output.summary,
                status="generated",
            )
            self.db.add(report)
        else:
            report.agent_trace_id = trace.id
            report.report_json = report_json
            report.summary = output.summary
            report.status = "generated"
        self.db.flush()
        return report


def _require_id(value: int | None, name: str) -> int:
    if value is None:
        raise ValueError(f"context.session.{name} is required for persistence")
    return value

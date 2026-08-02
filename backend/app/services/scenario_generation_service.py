from __future__ import annotations

from datetime import datetime, timedelta
from time import perf_counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.profile_agent import PROFILE_PROMPT_VERSION
from app.agents.interview_blueprint import (
    BLUEPRINT_VERSION,
    PRESENTATION_VERSION,
    blueprint_fingerprint,
    build_blueprint_from_generated,
)
from app.agents.runtime_interviewer_agent import (
    RUNTIME_INTERVIEWER_PROMPT_VERSION,
    InterviewerAgent,
)
from app.agents.scenario_design_agent import (
    GeneratedScenario,
    SCENARIO_PROMPT_VERSION,
    ScenarioAgentResult,
    ScenarioDesignAgent,
    normalize_occupation_key,
)
from app.core.config import get_settings
from app.core.database import get_sessionmaker
from app.models.agent import AgentTrace
from app.models.assessment import AssessmentSession, DialogueTurn
from app.models.participant import Participant, ParticipantProfile
from app.models.prompt import PromptTemplate
from app.models.scenario import Scenario, ScenarioGenerationJob, ScenarioStage
from app.services.scenario_materialization_service import (
    ScenarioMaterializationService,
)
from app.services.interview_state_service import InterviewStateService

STALE_JOB_SECONDS = 120


class ScenarioGenerationService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_job(
        self,
        *,
        session: AssessmentSession,
        category: str,
        occupation: str,
    ) -> ScenarioGenerationJob:
        cache_key = normalize_occupation_key(category, occupation)
        job = ScenarioGenerationJob(
            session_id=session.id,
            occupation_cache_key=cache_key,
            status="queued",
        )
        self.db.add(job)
        self.db.flush()
        return job

    def run_base_generation(self, session_id: int) -> None:
        job = self._job_for_session(session_id)
        # Only the worker that successfully observes the queued state may claim
        # the job. Stale in-flight jobs are reset to queued by resume_if_stale().
        if job is None or job.status != "queued":
            return
        session = self.db.get(AssessmentSession, session_id)
        if session is None:
            return
        participant = self.db.get(Participant, session.participant_id)
        if participant is None:
            return

        cached = self.db.execute(
            select(Scenario)
            .where(
                Scenario.source_type == "ai_base",
                Scenario.occupation_category == participant.industry,
                Scenario.occupation_key == job.occupation_cache_key,
                Scenario.generation_prompt_version == SCENARIO_PROMPT_VERSION,
                Scenario.status == "active",
            )
            .order_by(Scenario.created_at.desc(), Scenario.id.desc())
        ).scalars().first()
        if cached is not None:
            job.base_scenario_id = cached.id
            job.cache_hit = True
            job.status = "base_ready"
            job.locked_at = None
            self.db.commit()
            self.finalize_if_ready(session_id)
            return

        job.status = "drafting"
        job.locked_at = datetime.utcnow()
        self.db.commit()

        agent = ScenarioDesignAgent()
        design_prompt = self._active_prompt("scenario_design")
        started = perf_counter()
        draft_result = agent.generate_base(
            participant.industry or "待业/退休/其他",
            participant.career_direction or "当前身份",
            design_prompt.content if design_prompt else None,
        )
        job = self._job_for_session(session_id)
        if job is None:
            return
        job.design_call_count += 1
        self._trace_scenario_call(
            session=session,
            agent_name="scenario_design",
            result=draft_result,
            input_json={
                "occupation_category": participant.industry,
                "occupation": participant.career_direction,
                "prompt_version": SCENARIO_PROMPT_VERSION,
            },
            duration_ms=int((perf_counter() - started) * 1000),
        )
        draft_payload = (
            draft_result.scenario.model_dump()
            if draft_result.scenario is not None
            else draft_result.payload
        )
        if not draft_result.success or draft_payload is None:
            self._mark_fallback(job, draft_result)
            self.db.commit()
            self.finalize_if_ready(session_id)
            return

        job.draft_json = draft_payload
        job.status = "reviewing"
        job.locked_at = datetime.utcnow()
        self.db.commit()

        started = perf_counter()
        review_prompt = self._active_prompt("scenario_review")
        review_result = agent.review_base(
            participant.industry or "待业/退休/其他",
            participant.career_direction or "当前身份",
            draft_payload,
            review_prompt.content if review_prompt else None,
        )
        job = self._job_for_session(session_id)
        if job is None:
            return
        job.design_call_count += 1
        self._trace_scenario_call(
            session=session,
            agent_name="scenario_review",
            result=review_result,
            input_json={
                "occupation_category": participant.industry,
                "occupation": participant.career_direction,
                "draft": draft_payload,
                "prompt_version": SCENARIO_PROMPT_VERSION,
            },
            duration_ms=int((perf_counter() - started) * 1000),
        )
        if not review_result.success or review_result.scenario is None:
            self._mark_fallback(job, review_result)
            self.db.commit()
            self.finalize_if_ready(session_id)
            return

        base = ScenarioMaterializationService(self.db).materialize(
            review_result.scenario,
            scenario_code=None,
            source_type="ai_base",
            occupation_category=participant.industry,
            occupation_key=job.occupation_cache_key,
            model_name=review_result.model_name,
            base_scenario_id=None,
        )
        base.generation_metadata_json = {
            **(base.generation_metadata_json or {}),
            "occupation": participant.career_direction,
        }
        job.reviewed_json = review_result.scenario.model_dump()
        job.base_scenario_id = base.id
        job.status = "base_ready"
        job.locked_at = None
        self.db.commit()
        self.finalize_if_ready(session_id)

    def finalize_if_ready(self, session_id: int) -> bool:
        session = self.db.get(AssessmentSession, session_id)
        job = self._job_for_session(session_id)
        if session is None or job is None:
            return False
        if session.status in {"in_progress", "generating", "completed"}:
            return True
        profile = self.db.execute(
            select(ParticipantProfile).where(ParticipantProfile.session_id == session_id)
        ).scalar_one_or_none()
        if profile is None or not (profile.ai_profile_json or {}).get("completed"):
            return False
        if job.status in {"queued", "drafting", "reviewing"}:
            session.status = "scenario_preparing"
            self.db.commit()
            return False
        if job.status == "fallback":
            fallback = ScenarioMaterializationService(self.db).ensure_fallback()
            self._activate_session(session, fallback, "fallback", job.error_code)
            job.completed_at = datetime.utcnow()
            self.db.commit()
            return True
        # Adapting is an in-flight lock. A duplicate preparation poll must not
        # start a second per-session adaptation call.
        if job.status != "base_ready" or not job.base_scenario_id:
            return False

        job.status = "adapting"
        job.locked_at = datetime.utcnow()
        session.status = "scenario_preparing"
        self.db.commit()

        base = self.db.get(Scenario, job.base_scenario_id)
        if base is None:
            fallback = ScenarioMaterializationService(self.db).ensure_fallback()
            job.fallback_used = True
            job.error_code = "BASE_SCENARIO_MISSING"
            self._activate_session(session, fallback, "fallback", job.error_code)
            job.status = "fallback"
            job.completed_at = datetime.utcnow()
            self.db.commit()
            return True

        metadata = base.generation_metadata_json or {}
        generated_payload = metadata.get("generated_scenario")
        try:
            base_generated = GeneratedScenario.model_validate(generated_payload)
        except Exception as exc:  # noqa: BLE001
            job.error_code = "BASE_SCENARIO_INVALID"
            job.error_detail = str(exc)[:2000]
            self._activate_session(session, base, "occupation_cache", job.error_code)
            job.status = "completed"
            job.completed_at = datetime.utcnow()
            self.db.commit()
            return True

        started = perf_counter()
        adaptation_prompt = self._active_prompt("scenario_adaptation")
        result = ScenarioDesignAgent().adapt_for_profile(
            base_generated,
            profile.ai_profile_json or {},
            adaptation_prompt.content if adaptation_prompt else None,
        )
        job.adaptation_call_count += 1
        self._trace_scenario_call(
            session=session,
            agent_name="scenario_adaptation",
            result=result,
            input_json={
                "base_scenario_id": base.id,
                "profile": _safe_profile(profile.ai_profile_json or {}),
                "prompt_version": SCENARIO_PROMPT_VERSION,
            },
            duration_ms=int((perf_counter() - started) * 1000),
        )
        selected = base
        selection_mode = "occupation_cache"
        if result.success and result.scenario is not None:
            selected = ScenarioMaterializationService(self.db).materialize(
                result.scenario,
                scenario_code=None,
                source_type="ai_adapted",
                occupation_category=base.occupation_category,
                occupation_key=base.occupation_key,
                model_name=result.model_name,
                base_scenario_id=base.id,
            )
            selected.generation_metadata_json = {
                **(selected.generation_metadata_json or {}),
                "occupation": (base.generation_metadata_json or {}).get("occupation"),
            }
            job.adapted_scenario_id = selected.id
            selection_mode = "occupation_adapted"
        else:
            job.error_code = result.error_code
            job.error_detail = (result.error_reason or "")[:2000]
        self._activate_session(session, selected, selection_mode, job.error_code)
        job.status = "completed"
        job.locked_at = None
        job.completed_at = datetime.utcnow()
        self.db.commit()
        return True

    def resume_if_stale(self, session_id: int) -> bool:
        job = self._job_for_session(session_id)
        if job is None:
            return False
        if job.status == "queued":
            return True
        if job.status in {"drafting", "reviewing", "adapting"}:
            cutoff = datetime.utcnow() - timedelta(seconds=STALE_JOB_SECONDS)
            if job.locked_at is None or job.locked_at < cutoff:
                if job.status == "adapting":
                    job.status = "base_ready" if job.base_scenario_id else "fallback"
                else:
                    job.status = "queued"
                job.locked_at = None
                self.db.commit()
                return True
        return False

    def _job_for_session(self, session_id: int) -> ScenarioGenerationJob | None:
        return self.db.execute(
            select(ScenarioGenerationJob).where(
                ScenarioGenerationJob.session_id == session_id
            )
        ).scalar_one_or_none()

    @staticmethod
    def _mark_fallback(
        job: ScenarioGenerationJob, result: ScenarioAgentResult
    ) -> None:
        job.status = "fallback"
        job.fallback_used = True
        job.error_code = result.error_code or "SCENARIO_GENERATION_FAILED"
        job.error_detail = (result.error_reason or "")[:2000]
        job.locked_at = None

    def _activate_session(
        self,
        session: AssessmentSession,
        scenario: Scenario,
        selection_mode: str,
        error_code: str | None,
    ) -> None:
        stage = self.db.execute(
            select(ScenarioStage)
            .where(
                ScenarioStage.scenario_id == scenario.id,
                ScenarioStage.status == "active",
            )
            .order_by(ScenarioStage.stage_order)
        ).scalars().first()
        if stage is None:
            raise ValueError("selected scenario has no active stage")
        participant = self.db.get(Participant, session.participant_id)
        turn_index = (
            self.db.execute(
                select(DialogueTurn.turn_index)
                .where(DialogueTurn.session_id == session.id)
                .order_by(DialogueTurn.turn_index.desc())
            ).scalars().first()
            or 0
        ) + 1
        session.scenario_id = scenario.id
        session.current_stage_id = stage.id
        session.selection_mode = selection_mode
        session.selection_reason = (
            "occupation-adaptive scenario"
            if not error_code
            else f"occupation scenario degraded: {error_code}"
        )
        session.status = "in_progress"
        session.started_at = datetime.utcnow()
        if session.flow_version == "progressive_v3":
            blueprint = InterviewStateService.blueprint(scenario)
            if blueprint is None:
                generated_payload = (scenario.generation_metadata_json or {}).get(
                    "generated_scenario"
                )
                if generated_payload:
                    generated = GeneratedScenario.model_validate(generated_payload)
                    blueprint = build_blueprint_from_generated(
                        generated,
                        occupation_category=scenario.occupation_category,
                        occupation=(
                            (scenario.generation_metadata_json or {}).get("occupation")
                            or (participant.career_direction if participant else None)
                        ),
                    )
                    scenario.generation_metadata_json = {
                        **(scenario.generation_metadata_json or {}),
                        "interview_blueprint_version": BLUEPRINT_VERSION,
                        "interview_presentation_version": PRESENTATION_VERSION,
                        "interview_blueprint_fingerprint": blueprint_fingerprint(
                            blueprint
                        ),
                        "interview_blueprint": blueprint.model_dump(mode="json"),
                    }
            if blueprint is None:
                raise ValueError("progressive v3 blueprint is unavailable")
            InterviewStateService.initialize(session, scenario)
            opening = InterviewerAgent().render_opening(
                blueprint,
                participant.nickname if participant else "受测者",
            )
            prompt = self._active_prompt("interviewer")
            trace = AgentTrace(
                session_id=session.id,
                stage_id=stage.id,
                trigger_turn_id=None,
                prompt_template_id=prompt.id if prompt else None,
                agent_name="interviewer",
                generation_mode="deterministic_opening",
                ai_generation_weight=0,
                config_snapshot_json={
                    "prompt_version": RUNTIME_INTERVIEWER_PROMPT_VERSION,
                    "flow_version": session.flow_version,
                    "action": "OPENING",
                    "release_event_code": "opening_context",
                },
                input_json={
                    "blueprint_version": blueprint.schema_version,
                    "event_code": "opening_context",
                },
                output_json=opening.model_dump(mode="json"),
                raw_output=opening.model_dump_json(),
                status="success",
                model_name="deterministic",
                duration_ms=0,
            )
            self.db.add(trace)
            self.db.flush()
            self.db.add(
                DialogueTurn(
                    session_id=session.id,
                    stage_id=stage.id,
                    turn_index=turn_index,
                    speaker="ai",
                    content=opening.message,
                    content_type="interview_opening",
                    source_agent_trace_id=trace.id,
                )
            )
            return
        self.db.add(
            DialogueTurn(
                session_id=session.id,
                stage_id=stage.id,
                turn_index=turn_index,
                speaker="ai",
                content=_opening_message(
                    participant.nickname if participant else "受测者",
                    scenario.background,
                    stage.context,
                    stage.main_question,
                ),
                content_type="stage_question",
            )
        )

    def _trace_scenario_call(
        self,
        *,
        session: AssessmentSession,
        agent_name: str,
        result: ScenarioAgentResult,
        input_json: dict[str, Any],
        duration_ms: int,
    ) -> None:
        prompt = self.db.execute(
            select(PromptTemplate)
            .where(
                PromptTemplate.agent_name == agent_name,
                PromptTemplate.status == "active",
            )
            .order_by(PromptTemplate.updated_at.desc(), PromptTemplate.id.desc())
        ).scalars().first()
        self.db.add(
            AgentTrace(
                session_id=session.id,
                stage_id=None,
                trigger_turn_id=None,
                prompt_template_id=prompt.id if prompt else None,
                agent_name=agent_name,
                generation_mode=get_settings().MODEL_GATEWAY_MODE.lower(),
                ai_generation_weight=100,
                config_snapshot_json={
                    "prompt_version": SCENARIO_PROMPT_VERSION,
                    "shared_model_gateway": True,
                },
                input_json=input_json,
                output_json=(
                    result.scenario.model_dump()
                    if result.scenario is not None
                    else result.payload
                ),
                raw_output=result.raw_output,
                status="success" if result.success else "failed",
                error_code=result.error_code,
                model_name=result.model_name,
                duration_ms=max(duration_ms, 0),
            )
        )

    def _active_prompt(self, agent_name: str) -> PromptTemplate | None:
        return self.db.execute(
            select(PromptTemplate)
            .where(
                PromptTemplate.agent_name == agent_name,
                PromptTemplate.status == "active",
            )
            .order_by(PromptTemplate.updated_at.desc(), PromptTemplate.id.desc())
        ).scalars().first()


def run_base_generation_background(session_id: int) -> None:
    session_factory = get_sessionmaker()
    with session_factory() as db:
        try:
            ScenarioGenerationService(db).run_base_generation(session_id)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            job = db.execute(
                select(ScenarioGenerationJob).where(
                    ScenarioGenerationJob.session_id == session_id
                )
            ).scalar_one_or_none()
            if job is not None:
                job.status = "fallback"
                job.fallback_used = True
                job.error_code = "BACKGROUND_GENERATION_ERROR"
                job.error_detail = str(exc)[:2000]
                job.locked_at = None
                db.commit()
                ScenarioGenerationService(db).finalize_if_ready(session_id)


def finalize_scenario_background(session_id: int) -> None:
    session_factory = get_sessionmaker()
    with session_factory() as db:
        try:
            ScenarioGenerationService(db).finalize_if_ready(session_id)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            job = db.execute(
                select(ScenarioGenerationJob).where(
                    ScenarioGenerationJob.session_id == session_id
                )
            ).scalar_one_or_none()
            if job is not None:
                job.status = "fallback"
                job.fallback_used = True
                job.error_code = "BACKGROUND_ADAPTATION_ERROR"
                job.error_detail = str(exc)[:2000]
                job.locked_at = None
                db.commit()
                ScenarioGenerationService(db).finalize_if_ready(session_id)


def _safe_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        key: profile.get(key)
        for key in (
            "common_tasks",
            "collaborators",
            "familiar_decision_context",
            "summary",
        )
    }


def _opening_message(
    nickname: str, background: str, context: str, question: str
) -> str:
    return (
        f"{nickname}，谢谢你刚才的介绍。下面进入正式测评。请只根据题内信息回答，"
        "不需要调用专业规范，也不需要追求唯一标准答案。"
        f"\n\n【情境背景】\n{background}"
        f"\n\n【当前信息】\n{context}"
        f"\n\n【问题】\n{question}"
    )


__all__ = [
    "ScenarioGenerationService",
    "finalize_scenario_background",
    "run_base_generation_background",
]

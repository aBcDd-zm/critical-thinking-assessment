from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.schemas import (
    DimensionScore,
    EvidenceItem,
    MeasurementQuality,
    ScoringOutput,
)
from app.agents.user_turn_intent import (
    classify_progressive_control_intent,
    classify_user_turn,
    is_scoring_analysis,
)
from app.models.agent import AgentTrace
from app.models.assessment import AssessmentSession, DialogueTurn


@dataclass(frozen=True)
class DimensionEvidenceSufficiency:
    index: int | None
    level: str | None
    score_kind: str
    note: str


class EvidenceSufficiencyService:
    """Project-defined audit index; it is not reliability or score confidence."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def apply_scoring(
        self,
        session: AssessmentSession,
        output: ScoringOutput,
    ) -> tuple[ScoringOutput, MeasurementQuality]:
        state = session.interview_state_json or {}
        scored: list[DimensionScore] = []
        for item in output.scores:
            esi = self.dimension_result(state, item.dimension_key)
            score = item.score
            status = item.assessment_status
            evidence = list(item.evidence)
            if esi.score_kind == "unobserved":
                score = None
                status = "insufficient_evidence"
                reason = "没有释放对应情境或没有获得公平作答机会，未测到该维度。"
            elif esi.score_kind == "provisional":
                score = None
                status = "insufficient_evidence"
                reason = (
                    "已出现部分相关表达，但证据槽位未达到充分性门槛，"
                    "本次不作能力评分。"
                )
                evidence = self._provisional_evidence(
                    session,
                    state,
                    item.dimension_key,
                    existing_evidence=evidence,
                    explanation=reason,
                )
            elif score is None:
                weak_ids = (state.get("weak_evidence_turn_ids") or {}).get(
                    item.dimension_key, []
                )
                has_valid_observation = any(
                    observation.get("dimension_key") == item.dimension_key
                    and observation.get("validity") == "valid"
                    for entry in state.get("evidence_timeline") or []
                    for observation in entry.get("observations", [])
                )
                score = (
                    3 if has_valid_observation and not weak_ids
                    else 2 if has_valid_observation
                    else 1
                )
                status = "scored"
                reason = (
                    "已呈现少量相关行为，但关键关系仍不完整，对应2分标准。"
                    if has_valid_observation
                    else
                    "在公平作答机会下给出了实质回答，但未呈现该维度的"
                    "关键观察行为，对应1分标准。"
                )
                if not evidence:
                    slot = (state.get("dimension_slots") or {}).get(
                        item.dimension_key, {}
                    )
                    candidate_turn_ids = list(
                        dict.fromkeys(
                            [
                                *weak_ids,
                                *(slot.get("evidence_turn_ids") or []),
                            ]
                        )
                    )
                    turns = list(
                        self.db.execute(
                            select(DialogueTurn)
                            .where(
                                DialogueTurn.session_id == session.id,
                                DialogueTurn.id.in_(candidate_turn_ids),
                                DialogueTurn.speaker == "user",
                            )
                            .order_by(DialogueTurn.turn_index)
                        ).scalars()
                    ) if candidate_turn_ids else []
                    evidence = [
                        EvidenceItem(
                            text=turn.content,
                            evidence_type="weak_evidence",
                            explanation=reason,
                            dialogue_turn_id=turn.id,
                        )
                        for turn in turns[:2]
                    ]
            else:
                reason = item.reason
            scored.append(
                item.model_copy(
                    update={
                        "score": score,
                        "assessment_status": status,
                        "evidence": (
                            evidence
                            if score is not None or esi.score_kind == "provisional"
                            else []
                        ),
                        "confidence": None,
                        "evidence_sufficiency_index": esi.index,
                        "evidence_sufficiency_level": esi.level,
                        "score_kind": esi.score_kind,
                        "evidence_sufficiency_note": esi.note,
                        "reason": reason,
                    }
                )
            )
        quality = self.measurement_quality(session, scored)
        return output.model_copy(update={"scores": scored}), quality

    def _provisional_evidence(
        self,
        session: AssessmentSession,
        state: dict,
        dimension_key: str,
        *,
        existing_evidence: list[EvidenceItem],
        explanation: str,
    ) -> list[EvidenceItem]:
        """Return at most two auditable partial-evidence turns for a dimension.

        A provisional result remains unscored.  The citations only explain why
        the dimension is partial rather than unobserved: every returned item is
        rebuilt from a persisted, scoring-eligible user turn in this session.
        """

        slot = (state.get("dimension_slots") or {}).get(dimension_key) or {}
        candidate_ids = self._turn_ids(slot.get("evidence_turn_ids") or [])
        for entry in state.get("evidence_timeline") or []:
            entry_turn_id = entry.get("turn_id")
            for observation in entry.get("observations") or []:
                if (
                    observation.get("dimension_key") != dimension_key
                    or observation.get("validity") != "valid"
                    or observation.get("introduced_by_ai") is True
                    or observation.get("disposition") == "excluded"
                    or observation.get("response_origin") == "not_scored"
                ):
                    continue
                source_turn_id = observation.get("source_turn_id") or entry_turn_id
                candidate_ids.extend(self._turn_ids([source_turn_id]))

        candidate_ids = list(dict.fromkeys(candidate_ids))
        if not candidate_ids:
            return []

        turns = list(
            self.db.execute(
                select(DialogueTurn)
                .where(
                    DialogueTurn.session_id == session.id,
                    DialogueTurn.id.in_(candidate_ids),
                    DialogueTurn.speaker == "user",
                )
                .order_by(DialogueTurn.turn_index)
            ).scalars()
        )
        eligible_turns = [
            turn
            for turn in turns
            if turn.session_id == session.id
            and turn.speaker == "user"
            and self._is_scoring_turn(turn)
        ]
        turn_by_id = {turn.id: turn for turn in eligible_turns}

        preferred_ids: list[int] = []
        for item in existing_evidence:
            if item.evidence_type == "invalid_evidence":
                continue
            turn_id = next(iter(self._turn_ids([item.dialogue_turn_id])), None)
            turn = turn_by_id.get(turn_id)
            if turn is None:
                continue
            evidence_text = item.text.strip()
            if evidence_text and evidence_text not in turn.content:
                continue
            preferred_ids.append(turn.id)

        selected_ids = list(
            dict.fromkeys(
                [
                    *preferred_ids,
                    *(turn.id for turn in eligible_turns),
                ]
            )
        )[:2]
        return [
            EvidenceItem(
                text=turn_by_id[turn_id].content,
                evidence_type="weak_evidence",
                explanation=explanation,
                dialogue_turn_id=turn_id,
            )
            for turn_id in selected_ids
        ]

    @staticmethod
    def _turn_ids(values: list[object]) -> list[int]:
        turn_ids: list[int] = []
        for value in values:
            if isinstance(value, bool):
                continue
            try:
                turn_id = int(value)
            except (TypeError, ValueError):
                continue
            if turn_id > 0:
                turn_ids.append(turn_id)
        return turn_ids

    @staticmethod
    def _is_scoring_turn(turn: DialogueTurn) -> bool:
        if turn.analysis_json:
            return is_scoring_analysis(turn.analysis_json, text=turn.content)
        return classify_user_turn(turn.content) == "substantive_answer"

    @staticmethod
    def dimension_result(
        state: dict,
        dimension_key: str,
    ) -> DimensionEvidenceSufficiency:
        slots = state.get("dimension_slots") or {}
        slot = slots.get(dimension_key) or {}
        opportunity_counts = state.get("dimension_opportunity_counts") or {}
        opportunity_quality = state.get("dimension_opportunity_quality") or {}
        weak_ids = (state.get("weak_evidence_turn_ids") or {}).get(
            dimension_key, []
        )
        timeline = state.get("evidence_timeline") or []
        observations = [
            observation
            for entry in timeline
            for observation in entry.get("observations", [])
            if observation.get("dimension_key") == dimension_key
        ]
        valid = [item for item in observations if item.get("validity") == "valid"]
        weak = [item for item in observations if item.get("validity") == "weak"]
        opportunity_count = int(opportunity_counts.get(dimension_key, 0) or 0)
        if opportunity_count == 0 and not valid and not weak and not weak_ids:
            return DimensionEvidenceSufficiency(
                index=None,
                level=None,
                score_kind="unobserved",
                note="未释放对应情境或未获得公平作答机会，因此显示未测到。",
            )

        opportunity = int(opportunity_quality.get(dimension_key, 0) or 0)
        if opportunity not in {0, 15, 25}:
            opportunity = 25 if opportunity >= 20 else 15 if opportunity else 0
        if opportunity == 0 and (valid or weak):
            opportunity = 15
        relevance = 20 if valid else 10 if weak or weak_ids else 0
        valid_behaviors = {
            item.get("behavior_key") for item in valid if item.get("behavior_key")
        }
        specificity = (
            25 if len(valid_behaviors) >= 2
            else 15 if len(valid_behaviors) == 1
            else 5 if weak or weak_ids
            else 0
        )
        slot_status = slot.get("status")
        coverage = 20 if slot_status == "sufficient" else 10 if (
            slot_status == "partial" or valid or weak or weak_ids
        ) else 0
        has_conflict = bool(slot.get("conflicting_evidence_turn_ids"))
        technical_fallbacks = int(state.get("technical_fallback_count", 0) or 0)
        integrity = 0 if opportunity == 0 else 5 if (
            has_conflict or technical_fallbacks
        ) else 10
        index = opportunity + relevance + specificity + coverage + integrity
        if has_conflict:
            index = min(index, 74)
        if (
            slot_status == "blocked"
            and technical_fallbacks
            and slot.get("insufficient_reason") == "technical_failure"
        ):
            index = min(index, 49)
        index = max(0, min(index, 100))
        score_kind = "supported" if slot_status == "sufficient" else "provisional"
        return DimensionEvidenceSufficiency(
            index=index,
            level=_esi_level(index),
            score_kind=score_kind,
            note=(
                "表示本次能力判断的证据基础，不代表结论有相同百分比的概率正确。"
            ),
        )

    def measurement_quality(
        self,
        session: AssessmentSession,
        scores: list[DimensionScore] | None = None,
    ) -> MeasurementQuality:
        traces = list(
            self.db.execute(
                select(AgentTrace).where(
                    AgentTrace.session_id == session.id,
                    AgentTrace.agent_name == "consultative_turn",
                )
            ).scalars()
        )
        has_component_contract = any(
            (trace.config_snapshot_json or {}).get("measurement_core_status")
            for trace in traces
        )
        if has_component_contract:
            measurement_traces = [
                trace
                for trace in traces
                if (trace.config_snapshot_json or {}).get("measurement_scope")
                == "formal_answer"
            ]
            total = len(measurement_traces)
            fallback_count = sum(
                1
                for trace in measurement_traces
                if trace.status in {"fallback", "failed"}
            )
            technical_count = sum(
                1
                for trace in measurement_traces
                if (trace.config_snapshot_json or {}).get(
                    "measurement_core_status"
                )
                == "failed"
            )
        else:
            # Historical sessions used a compound model call and did not
            # distinguish formal measurement from opening/repair traces.
            measurement_traces = traces
            total = len(measurement_traces)
            fallback_count = sum(
                1
                for trace in measurement_traces
                if trace.status in {"fallback", "failed"}
            )
            technical_count = sum(
                1
                for trace in measurement_traces
                if (trace.config_snapshot_json or {}).get("model_call_status")
                == "failed"
                or trace.error_code in {
                    "CONSULTATIVE_TURN_FALLBACK",
                    "MISSING_TURN_PLAN",
                }
            )
        technical_rate = technical_count / total if total else 0
        fallback_rate = fallback_count / total if total else 0
        state = session.interview_state_json or {}
        released = set(state.get("released_event_codes") or [])
        missing_events = [
            event for event in ("counter_evidence", "integration")
            if event not in released
        ]
        if scores is None:
            dimension_results = {
                dimension_key: self.dimension_result(state, dimension_key)
                for dimension_key in (state.get("dimension_slots") or {})
            }
            unobserved_dimensions = [
                key
                for key, result in dimension_results.items()
                if result.score_kind == "unobserved"
            ]
            provisional_dimensions = [
                key
                for key, result in dimension_results.items()
                if result.score_kind == "provisional"
            ]
        else:
            unobserved_dimensions = [
                item.dimension_key
                for item in scores
                if item.score_kind == "unobserved"
            ]
            provisional_dimensions = [
                item.dimension_key
                for item in scores
                if item.score_kind == "provisional"
            ]
        contamination_turn_ids = self._scoring_contamination_turn_ids(session)
        has_measurement_gap = bool(
            missing_events
            or unobserved_dimensions
            or provisional_dimensions
        )
        reasons: list[str] = []
        invalid = False
        if contamination_turn_ids:
            invalid = True
            reasons.append("对话修复或情境澄清内容进入了正式评分证据链")
        if technical_rate >= 0.30 and has_measurement_gap:
            invalid = True
            reasons.append("技术回退率达到或超过30%")
        elif technical_rate >= 0.30:
            reasons.append(
                "技术回退率较高，但确定性降级已保留完整事件和六维证据，"
                "结果需谨慎解释"
            )
        if fallback_rate >= 0.50:
            invalid = True
            reasons.append("总回退率达到或超过50%")
        if missing_events and int(state.get("technical_fallback_count", 0) or 0) > 0:
            invalid = True
            reasons.append("系统问题导致反证或整合事件未完成")
        if len(unobserved_dimensions) >= 3:
            invalid = True
            reasons.append("半数及以上维度未获得公平作答机会")
        if invalid:
            status = "invalid"
        elif (
            fallback_count
            or technical_rate >= 0.30
            or missing_events
            or unobserved_dimensions
            or provisional_dimensions
        ):
            status = "caution"
            if fallback_count or missing_events:
                reasons.append("部分测量环节不完整，解释时需谨慎")
            if unobserved_dimensions:
                reasons.append("存在未测到维度，不得解释为能力不足")
            if provisional_dimensions:
                reasons.append("部分维度证据未达到充分性门槛，暂不评分")
        else:
            status = "valid"

        scored_esi = [
            item.evidence_sufficiency_index
            for item in (scores or [])
            if item.score is not None and item.evidence_sufficiency_index is not None
        ]
        overall_esi = (
            round(sum(scored_esi) / len(scored_esi) * len(scored_esi) / 6, 1)
            if scored_esi
            else None
        )
        return MeasurementQuality(
            status=status,
            technical_failure_rate=round(technical_rate, 4),
            total_fallback_rate=round(fallback_rate, 4),
            missing_events=missing_events,
            unobserved_dimensions=unobserved_dimensions,
            provisional_dimensions=provisional_dimensions,
            scoring_contamination_turn_ids=contamination_turn_ids,
            retest_recommended=invalid or bool(unobserved_dimensions),
            reasons=reasons,
            overall_evidence_sufficiency_index=overall_esi,
        )

    def _scoring_contamination_turn_ids(
        self,
        session: AssessmentSession,
    ) -> list[int]:
        state = session.interview_state_json or {}
        tracked_turn_ids = {
            int(entry["turn_id"])
            for entry in state.get("evidence_timeline") or []
            if entry.get("turn_id") is not None
            and (entry.get("observations") or [])
        }
        for slot in (state.get("dimension_slots") or {}).values():
            tracked_turn_ids.update(slot.get("evidence_turn_ids") or [])
            tracked_turn_ids.update(slot.get("conflicting_evidence_turn_ids") or [])

        turns = list(
            self.db.execute(
                select(DialogueTurn).where(
                    DialogueTurn.session_id == session.id,
                    DialogueTurn.speaker == "user",
                )
            ).scalars()
        )
        contaminated: list[int] = []
        for turn in turns:
            if classify_progressive_control_intent(turn.content) is None:
                continue
            analysis = turn.analysis_json or {}
            if (
                analysis.get("formal_answer") is True
                or analysis.get("excluded_from_scoring") is not True
                or turn.id in tracked_turn_ids
            ):
                contaminated.append(turn.id)
        return contaminated


def _esi_level(index: int) -> str:
    if index >= 85:
        return "high"
    if index >= 60:
        return "medium"
    return "low"


__all__ = [
    "DimensionEvidenceSufficiency",
    "EvidenceSufficiencyService",
]

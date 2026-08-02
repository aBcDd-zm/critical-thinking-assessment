from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.agents.behavior_signal_extractor import extract_behavior_evidence_spans
from app.agents.measurement_contract import (
    DimensionMeasurementRule,
    load_measurement_contract,
)
from app.agents.progressive_schemas import (
    EvidenceDeltaAudit,
    EvidenceObservation,
    EvidenceResponseOrigin,
    InterviewState,
)

EVIDENCE_POLICY_VERSION = "ai_copy_exclusion_v1"
_CJK_CHARACTER_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


class EvidenceTrackerService:
    def __init__(self) -> None:
        self.contract = load_measurement_contract()
        self.rules = {item.dimension_key: item for item in self.contract.dimensions}

    def apply(
        self,
        state: InterviewState,
        *,
        turn_id: int,
        observations: list[EvidenceObservation],
    ) -> list[EvidenceDeltaAudit]:
        grouped: dict[str, list[EvidenceObservation]] = {}
        for observation in observations:
            if (
                observation.introduced_by_ai
                or observation.disposition == "excluded"
                or observation.response_origin == "not_scored"
            ):
                continue
            rule = self.rules.get(observation.dimension_key)
            if rule is None:
                continue
            known_behaviors = {item.behavior_key for item in rule.behaviors}
            if observation.behavior_key not in known_behaviors:
                continue
            grouped.setdefault(observation.dimension_key, []).append(observation)

        deltas: list[EvidenceDeltaAudit] = []
        for dimension_key, slot in state.dimension_slots.items():
            rule = self.rules[dimension_key]
            before = slot.status
            added_behaviors: list[str] = []
            repeated_turns: list[int] = []
            conflicts: list[int] = []
            confidences: list[float] = []

            for observation in grouped.get(dimension_key, []):
                confidences.append(observation.extraction_confidence)
                if observation.validity == "weak":
                    if turn_id not in slot.diagnostic_low_evidence_turn_ids:
                        slot.diagnostic_low_evidence_turn_ids.append(turn_id)
                    continue
                if observation.validity != "valid":
                    continue
                if observation.novelty == "contradictory":
                    if turn_id not in slot.conflicting_evidence_turn_ids:
                        slot.conflicting_evidence_turn_ids.append(turn_id)
                    conflicts.append(turn_id)
                    continue
                if observation.behavior_key in slot.observed_behavior_keys:
                    repeated_turns.append(turn_id)
                    continue
                slot.observed_behavior_keys.append(observation.behavior_key)
                added_behaviors.append(observation.behavior_key)
                if turn_id not in slot.evidence_turn_ids:
                    slot.evidence_turn_ids.append(turn_id)

            slot.missing_behavior_keys = [
                item.behavior_key
                for item in rule.behaviors
                if item.behavior_key not in slot.observed_behavior_keys
            ]
            if slot.status != "not_available":
                next_status = self._status_for(
                    rule,
                    slot.observed_behavior_keys,
                    evidence_turn_ids=slot.evidence_turn_ids,
                    diagnostic_low_evidence_turn_ids=(
                        slot.diagnostic_low_evidence_turn_ids
                    ),
                    has_unresolved_conflict=bool(slot.conflicting_evidence_turn_ids),
                )
                if (
                    before == "sufficient"
                    and next_status in {"not_started", "partial"}
                    and not slot.conflicting_evidence_turn_ids
                ):
                    next_status = "sufficient"
                slot.status = next_status

            delta_type = self._delta_type(
                before,
                slot.status,
                added_behaviors=added_behaviors,
                repeated_turns=repeated_turns,
                conflicts=conflicts,
            )
            delta = EvidenceDeltaAudit(
                dimension_key=dimension_key,
                status_before=before,
                status_after=slot.status,
                delta_type=delta_type,
                added_evidence_turn_ids=[turn_id] if added_behaviors else [],
                added_behavior_keys=added_behaviors,
                repeated_evidence_turn_ids=list(dict.fromkeys(repeated_turns)),
                added_conflicting_turn_ids=list(dict.fromkeys(conflicts)),
                extraction_confidences=confidences,
            )
            deltas.append(delta)

        state.evidence_timeline.append(
            {
                "turn_id": turn_id,
                "observations": [
                    item.model_dump(mode="json") for item in observations
                ],
                "deltas": [item.model_dump(mode="json") for item in deltas],
            }
        )
        return deltas

    @staticmethod
    def classify_response_origin(
        *,
        formal_answer: bool,
        preceding_ai_content_type: str | None,
    ) -> EvidenceResponseOrigin:
        if not formal_answer:
            return "not_scored"
        if preceding_ai_content_type in {
            "interview_opening",
            "interview_event",
        }:
            return "spontaneous_evidence"
        if preceding_ai_content_type in {
            "interview_followup",
            "interview_integration",
            "interview_clarification",
        }:
            return "elicited_evidence"
        if preceding_ai_content_type in {
            "interview_closing",
        }:
            return "not_scored"
        # Imported and legacy sessions may not retain a typed preceding AI turn.
        # Keep those answers auditable without claiming that they were spontaneous.
        return "elicited_evidence"

    @classmethod
    def annotate_observations(
        cls,
        observations: list[EvidenceObservation],
        *,
        response_origin: EvidenceResponseOrigin,
        source_turn_id: int,
        preceding_ai_turn_id: int | None,
        preceding_ai_text: str | None,
        earlier_user_texts: list[str],
        source_text: str | None = None,
    ) -> list[EvidenceObservation]:
        earlier_user_normalized = [
            cls._normalize_evidence_text(text)
            for text in earlier_user_texts
            if text.strip()
        ]
        audited: list[EvidenceObservation] = []
        for observation in observations:
            overlaps = cls._non_exempt_ai_overlaps(
                observation.quote,
                preceding_ai_text or "",
                earlier_user_normalized=earlier_user_normalized,
            )
            replacement_quote = None
            if overlaps:
                replacement_quote = cls._independent_behavior_support(
                    source_text or observation.quote,
                    excluded_spans=overlaps,
                    dimension_key=observation.dimension_key,
                    behavior_key=observation.behavior_key,
                )
            introduced_by_ai = bool(overlaps and replacement_quote is None)
            excluded_for_origin = response_origin == "not_scored"
            excluded = introduced_by_ai or excluded_for_origin
            exclusion_reason = (
                "introduced_verbatim_by_preceding_ai"
                if introduced_by_ai
                else "non_measurement_response_origin"
                if excluded_for_origin
                else None
            )
            audited_quote = (
                replacement_quote
                if replacement_quote is not None
                else max(overlaps, key=len)
                if introduced_by_ai
                else observation.quote
            )
            audited.append(
                observation.model_copy(
                    update={
                        "quote": audited_quote,
                        "validity": "invalid" if excluded else observation.validity,
                        "response_origin": response_origin,
                        "source_turn_id": source_turn_id,
                        "preceding_ai_turn_id": preceding_ai_turn_id,
                        "introduced_by_ai": introduced_by_ai,
                        "disposition": "excluded" if excluded else "accepted",
                        "exclusion_reason": exclusion_reason,
                        "evidence_policy_version": EVIDENCE_POLICY_VERSION,
                    }
                )
            )
        return audited

    @classmethod
    def _non_exempt_ai_overlaps(
        cls,
        observation_quote: str,
        preceding_ai_text: str,
        *,
        earlier_user_normalized: list[str],
    ) -> list[str]:
        if not observation_quote or not preceding_ai_text:
            return []
        overlaps: list[str] = []
        matcher = SequenceMatcher(
            None,
            observation_quote,
            preceding_ai_text,
            autojunk=False,
        )
        for match in matcher.get_matching_blocks():
            if match.size <= 0:
                continue
            overlap = observation_quote[match.a : match.a + match.size]
            overlap = overlap.strip()
            overlap_normalized = cls._normalize_evidence_text(overlap)
            if len(_CJK_CHARACTER_RE.findall(overlap_normalized)) < 4:
                continue
            if any(
                overlap_normalized in earlier_text
                for earlier_text in earlier_user_normalized
                if overlap_normalized
            ):
                continue
            overlaps.append(overlap)
        return list(dict.fromkeys(overlaps))

    @staticmethod
    def _independent_behavior_support(
        source_text: str,
        *,
        excluded_spans: list[str],
        dimension_key: str,
        behavior_key: str,
    ) -> str | None:
        ranges: list[tuple[int, int]] = []
        for span in excluded_spans:
            start = 0
            while span and (index := source_text.find(span, start)) >= 0:
                ranges.append((index, index + len(span)))
                start = index + len(span)
        if not ranges:
            return None
        merged: list[tuple[int, int]] = []
        for start, end in sorted(ranges):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        boundaries = [0, *(value for pair in merged for value in pair), len(source_text)]
        candidates: list[str] = []
        for index in range(0, len(boundaries) - 1, 2):
            segment = source_text[boundaries[index] : boundaries[index + 1]]
            evidence = extract_behavior_evidence_spans(
                segment,
                allow_dynamic=True,
            )
            behavior_span = evidence.get(dimension_key, {}).get(behavior_key)
            if behavior_span is not None:
                candidates.append(behavior_span.quote)
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda quote: (
                len(quote),
                source_text.find(quote),
            ),
        )

    @staticmethod
    def _normalize_evidence_text(text: str) -> str:
        return "".join(text.split())

    @staticmethod
    def original_span_after_exclusions(
        source_text: str,
        excluded_quotes: list[str],
    ) -> str | None:
        """Return one untouched contiguous user span outside excluded AI copies."""

        ranges: list[tuple[int, int]] = []
        for quote in excluded_quotes:
            start = 0
            while quote and (index := source_text.find(quote, start)) >= 0:
                ranges.append((index, index + len(quote)))
                start = index + len(quote)
        if not ranges:
            candidate = source_text.strip()
            return candidate[:500] if candidate else None

        merged: list[tuple[int, int]] = []
        for start, end in sorted(ranges):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        boundaries = [0, *(value for pair in merged for value in pair), len(source_text)]
        candidates: list[str] = []
        for index in range(0, len(boundaries) - 1, 2):
            segment = source_text[boundaries[index] : boundaries[index + 1]]
            for clause in re.split(r"[，,；;。.!！？?\n]+", segment):
                candidate = clause.strip()
                if (
                    len(_CJK_CHARACTER_RE.findall(candidate)) >= 4
                    and extract_behavior_evidence_spans(
                        candidate,
                        allow_dynamic=True,
                    )
                ):
                    candidates.append(candidate)
        if not candidates:
            return None
        return max(candidates, key=len)[:500]

    def unlock_for_event(self, state: InterviewState, event_code: str) -> None:
        if event_code not in state.released_event_codes:
            state.released_event_codes.append(event_code)
        for rule in self.contract.dimensions:
            if event_code not in rule.becomes_available_on:
                continue
            slot = state.dimension_slots[rule.dimension_key]
            if slot.status == "not_available":
                slot.status = "not_started"

    @staticmethod
    def _status_for(
        rule: DimensionMeasurementRule,
        observed: list[str],
        *,
        evidence_turn_ids: list[int],
        diagnostic_low_evidence_turn_ids: list[int],
        has_unresolved_conflict: bool,
    ) -> str:
        observed_set = set(observed)
        positive = rule.sufficient_positive_when
        all_satisfied = set(positive.all_of_behavior_keys).issubset(observed_set)
        group_satisfied = any(
            bool(set(group) & observed_set)
            for group in positive.any_of_behavior_groups
        ) if positive.any_of_behavior_groups else True
        enough_turns = (
            len(set(evidence_turn_ids))
            >= positive.min_distinct_user_turns
        )
        conflict_ok = not (
            positive.require_no_unresolved_conflict and has_unresolved_conflict
        )
        if all_satisfied and group_satisfied and enough_turns and conflict_ok:
            return "sufficient"
        if (
            diagnostic_low_evidence_turn_ids
            and not has_unresolved_conflict
        ):
            return "sufficient"
        if observed_set:
            return "partial"
        return "not_started"

    @staticmethod
    def _delta_type(
        before: str,
        after: str,
        *,
        added_behaviors: list[str],
        repeated_turns: list[int],
        conflicts: list[int],
    ) -> str:
        if conflicts:
            return "contradictory"
        if before == "partial" and after == "sufficient":
            return "partial_to_sufficient"
        if added_behaviors and before == "not_started":
            return "new_partial" if after == "partial" else "partial_to_sufficient"
        if added_behaviors:
            return "corroborating"
        if repeated_turns:
            return "none"
        return "none"


__all__ = ["EVIDENCE_POLICY_VERSION", "EvidenceTrackerService"]

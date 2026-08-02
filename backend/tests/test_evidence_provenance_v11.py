from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from app.agents.progressive_schemas import (
    DimensionSlotState,
    EvidenceObservation,
    InterviewState,
)
from app.agents.consultative_turn_agent import ConsultativeTurnAgent
from app.agents.humanistic_interviewer_v11 import V11_OUTPUT_MARKER
from app.agents.interview_planner_agent import InterviewPlannerAgent
from app.agents.runtime_interviewer_agent import (
    BASELINE_INTERVIEWER_STYLE,
    HUMANISTIC_INTERVIEWER_STYLE_V1_1,
)
from app.agents.user_turn_intent import analyze_humanistic_authority_request
from app.models.assessment import AssessmentSession
from app.services.admin_session_review_service import _trace_audit_fields
from app.services.evidence_tracker_service import (
    EVIDENCE_POLICY_VERSION,
    EvidenceTrackerService,
)
from app.services.session_service import (
    _applied_interviewer_style,
    _default_interviewer_style,
)


def _observation(
    quote: str,
    *,
    behavior_key: str = "inspect_source_sample_quality",
) -> EvidenceObservation:
    return EvidenceObservation(
        dimension_key="evidence_evaluation",
        behavior_key=behavior_key,
        quote=quote,
        rationale="专项测试证据",
        extraction_confidence=0.82,
    )


class EvidenceProvenanceV11Tests(unittest.TestCase):
    def test_v11_minimum_spans_do_not_change_frozen_legacy_quotes(self) -> None:
        text = "故障风险很高；但我会先小范围试用并设置回滚条件。"

        legacy = InterviewPlannerAgent._mock_observations(text)  # noqa: SLF001
        v11 = InterviewPlannerAgent._v11_observations(text)  # noqa: SLF001

        self.assertEqual(
            {
                (item.dimension_key, item.behavior_key)
                for item in legacy
            },
            {
                (item.dimension_key, item.behavior_key)
                for item in v11
            },
        )
        self.assertTrue(legacy)
        self.assertTrue(all(item.quote == text for item in legacy))
        self.assertTrue(all(item.quote in text for item in v11))
        self.assertTrue(any(item.quote != text for item in v11))

    def test_v11_minimum_spans_preserve_legacy_evidence_slot_updates(self) -> None:
        text = "故障风险很高；但我会先小范围试用并设置回滚条件。"
        legacy = InterviewPlannerAgent._mock_observations(text)  # noqa: SLF001
        v11 = InterviewPlannerAgent._v11_observations(text)  # noqa: SLF001
        tracker = EvidenceTrackerService()
        dimensions = {
            item.dimension_key
            for item in [*legacy, *v11]
        }

        def state_for_observations() -> InterviewState:
            return InterviewState(
                current_node_code="s4_reasoning_decision",
                dimension_slots={
                    dimension: DimensionSlotState(
                        dimension_key=dimension,
                        status="not_started",
                        missing_behavior_keys=[
                            behavior.behavior_key
                            for behavior in tracker.rules[dimension].behaviors
                        ],
                    )
                    for dimension in dimensions
                },
            )

        legacy_state = state_for_observations()
        v11_state = state_for_observations()
        legacy_deltas = tracker.apply(
            legacy_state,
            turn_id=41,
            observations=legacy,
        )
        v11_deltas = tracker.apply(
            v11_state,
            turn_id=41,
            observations=v11,
        )

        self.assertEqual(
            [
                (
                    item.dimension_key,
                    item.status_after,
                    item.added_behavior_keys,
                )
                for item in legacy_deltas
            ],
            [
                (
                    item.dimension_key,
                    item.status_after,
                    item.added_behavior_keys,
                )
                for item in v11_deltas
            ],
        )
        self.assertEqual(
            {
                key: (
                    slot.status,
                    slot.observed_behavior_keys,
                    slot.evidence_turn_ids,
                )
                for key, slot in legacy_state.dimension_slots.items()
            },
            {
                key: (
                    slot.status,
                    slot.observed_behavior_keys,
                    slot.evidence_turn_ids,
                )
                for key, slot in v11_state.dimension_slots.items()
            },
        )

    def test_legacy_validation_cannot_use_v11_reflection_length_waiver(
        self,
    ) -> None:
        agent = ConsultativeTurnAgent()
        output = SimpleNamespace(
            message="为了继续了解你的判断，你接下来会怎么做？",
            warnings=["reflection_omitted_for_length"],
        )
        plan = SimpleNamespace(
            release_event_code=None,
            release_unit_code=None,
            reflection_basis_turn_ids=[],
        )
        blueprint = SimpleNamespace(
            event_cards=[],
            identity_constraints=None,
        )
        context = SimpleNamespace(dialogue_history=[])

        with patch(
            "app.agents.consultative_turn_agent."
            "InterviewQuestionValidator.validate",
            return_value=(False, ["missing_reflection"]),
        ):
            legacy_errors = agent.validate_turn(
                output,
                plan=plan,
                blueprint=blueprint,
                context=context,
                previous_questions=[],
                enforce_humanistic_safety=True,
            )
            output.warnings = [
                V11_OUTPUT_MARKER,
                "reflection_omitted_for_length",
            ]
            v11_errors = agent.validate_turn(
                output,
                plan=plan,
                blueprint=blueprint,
                context=context,
                previous_questions=[],
                enforce_humanistic_safety=True,
            )

        self.assertIn("missing_reflection", legacy_errors)
        self.assertNotIn("missing_reflection", v11_errors)

    def test_partial_ai_copy_without_independent_behavior_support_is_excluded(
        self,
    ) -> None:
        ai_text = "你会先看故障率数据吗？"
        user_text = "我会先看故障率数据，然后再说。"
        observations = InterviewPlannerAgent._v11_observations(  # noqa: SLF001
            user_text
        )
        audited = EvidenceTrackerService.annotate_observations(
            observations,
            response_origin="elicited_evidence",
            source_turn_id=82,
            preceding_ai_turn_id=81,
            preceding_ai_text=ai_text,
            earlier_user_texts=[],
            source_text=user_text,
        )

        self.assertEqual(len(audited), 1)
        self.assertEqual(audited[0].quote, "先看故障率数据")
        self.assertEqual(audited[0].validity, "invalid")
        self.assertEqual(audited[0].disposition, "excluded")
        self.assertTrue(audited[0].introduced_by_ai)
        self.assertIsNone(
            EvidenceTrackerService.original_span_after_exclusions(
                user_text,
                [audited[0].quote],
            )
        )

        tracker = EvidenceTrackerService()
        dimension = audited[0].dimension_key
        state = InterviewState(
            current_node_code="s4_reasoning_decision",
            dimension_slots={
                dimension: DimensionSlotState(
                    dimension_key=dimension,
                    status="not_started",
                    missing_behavior_keys=[
                        behavior.behavior_key
                        for behavior in tracker.rules[dimension].behaviors
                    ],
                )
            },
        )
        tracker.apply(state, turn_id=82, observations=audited)
        self.assertEqual(
            state.dimension_slots[dimension].observed_behavior_keys,
            [],
        )

    def test_partial_ai_copy_shrinks_to_independent_same_behavior_support(
        self,
    ) -> None:
        ai_text = "你会先看故障率数据吗？"
        user_text = "我会先看故障率数据，然后先核对用户日志再决定。"
        observations = InterviewPlannerAgent._v11_observations(  # noqa: SLF001
            user_text
        )
        target = next(
            item
            for item in observations
            if item.dimension_key == "integrative_decision"
            and item.behavior_key == "define_plan_priority_conditions"
        )
        self.assertIn("先看故障率数据", target.quote)

        audited = EvidenceTrackerService.annotate_observations(
            [target],
            response_origin="elicited_evidence",
            source_turn_id=92,
            preceding_ai_turn_id=91,
            preceding_ai_text=ai_text,
            earlier_user_texts=[],
            source_text=user_text,
        )
        item = audited[0]
        self.assertEqual(item.disposition, "accepted")
        self.assertFalse(item.introduced_by_ai)
        self.assertNotIn("先看故障率数据", item.quote)
        self.assertIn("先核对用户日志再", item.quote)
        self.assertIn(item.quote, user_text)

        tracker = EvidenceTrackerService()
        state = InterviewState(
            current_node_code="s4_reasoning_decision",
            dimension_slots={
                "integrative_decision": DimensionSlotState(
                    dimension_key="integrative_decision",
                    status="not_started",
                    missing_behavior_keys=[
                        behavior.behavior_key
                        for behavior in tracker.rules[
                            "integrative_decision"
                        ].behaviors
                    ],
                )
            },
        )
        tracker.apply(state, turn_id=92, observations=audited)
        self.assertIn(
            "define_plan_priority_conditions",
            state.dimension_slots[
                "integrative_decision"
            ].observed_behavior_keys,
        )

    def test_partial_overlap_is_exempt_when_user_said_it_before_ai(self) -> None:
        user_text = "我会先看故障率数据，然后再说。"
        observations = InterviewPlannerAgent._v11_observations(  # noqa: SLF001
            user_text
        )
        audited = EvidenceTrackerService.annotate_observations(
            observations,
            response_origin="elicited_evidence",
            source_turn_id=102,
            preceding_ai_turn_id=101,
            preceding_ai_text="你会先看故障率数据吗？",
            earlier_user_texts=["我之前已经说过会先看故障率数据。"],
            source_text=user_text,
        )
        self.assertEqual(audited[0].disposition, "accepted")
        self.assertFalse(audited[0].introduced_by_ai)
        self.assertEqual(audited[0].quote, observations[0].quote)

    def test_planner_emits_behavior_specific_minimum_source_spans(self) -> None:
        text = "故障风险很高；但我会先小范围试用并设置回滚条件。"
        observations = InterviewPlannerAgent._v11_observations(text)  # noqa: SLF001

        self.assertGreaterEqual(len(observations), 2)
        self.assertTrue(any("故障风险" in item.quote for item in observations))
        self.assertTrue(any("先小范围" in item.quote for item in observations))
        for item in observations:
            self.assertIn(item.quote, text)
            self.assertNotEqual(item.quote, text)
            self.assertGreaterEqual(
                sum("\u3400" <= character <= "\u9fff" for character in item.quote),
                4,
            )

    def test_planner_to_tracker_chain_excludes_copy_but_keeps_original_span(
        self,
    ) -> None:
        text = "故障风险很高；但我会先小范围试用并设置回滚条件。"
        observations = InterviewPlannerAgent._v11_observations(text)  # noqa: SLF001
        audited = EvidenceTrackerService.annotate_observations(
            observations,
            response_origin="elicited_evidence",
            source_turn_id=52,
            preceding_ai_turn_id=51,
            preceding_ai_text="你提到故障风险很高。接下来你会怎么做？",
            earlier_user_texts=[],
        )

        excluded = [item for item in audited if item.disposition == "excluded"]
        accepted = [item for item in audited if item.disposition == "accepted"]
        self.assertTrue(excluded)
        self.assertTrue(accepted)
        self.assertTrue(all(item.introduced_by_ai for item in excluded))
        self.assertTrue(any("先小范围" in item.quote for item in accepted))

        tracker = EvidenceTrackerService()
        dimensions = {item.dimension_key for item in audited}
        state = InterviewState(
            current_node_code="s4_reasoning_decision",
            dimension_slots={
                dimension: DimensionSlotState(
                    dimension_key=dimension,
                    status="not_started",
                    missing_behavior_keys=[
                        behavior.behavior_key
                        for behavior in tracker.rules[dimension].behaviors
                    ],
                )
                for dimension in dimensions
            },
        )
        tracker.apply(state, turn_id=52, observations=audited)
        accepted_keys = {
            (item.dimension_key, item.behavior_key)
            for item in accepted
        }
        excluded_keys = {
            (item.dimension_key, item.behavior_key)
            for item in excluded
        }
        observed_keys = {
            (dimension, behavior)
            for dimension, slot in state.dimension_slots.items()
            for behavior in slot.observed_behavior_keys
        }
        self.assertTrue(accepted_keys & observed_keys)
        self.assertFalse(excluded_keys & observed_keys)
        self.assertEqual(
            state.evidence_timeline[-1]["observations"],
            [item.model_dump(mode="json") for item in audited],
        )

    def test_contamination_requires_the_full_extracted_span_not_fuzzy_overlap(
        self,
    ) -> None:
        audited = EvidenceTrackerService.annotate_observations(
            [_observation("我会先小范围试用")],
            response_origin="elicited_evidence",
            source_turn_id=62,
            preceding_ai_turn_id=61,
            preceding_ai_text="可以先做小范围测试，再看情况。",
            earlier_user_texts=[],
        )
        self.assertEqual(audited[0].disposition, "accepted")
        self.assertFalse(audited[0].introduced_by_ai)

    def test_planner_span_keeps_earlier_user_wording_exemption(self) -> None:
        observations = InterviewPlannerAgent._v11_observations(  # noqa: SLF001
            "故障风险很高；我会先核实日志。"
        )
        audited = EvidenceTrackerService.annotate_observations(
            observations,
            response_origin="elicited_evidence",
            source_turn_id=72,
            preceding_ai_turn_id=71,
            preceding_ai_text="你刚才提到故障风险很高。",
            earlier_user_texts=["前面我已经说过故障风险很高。"],
        )
        copied_spans = [
            item
            for item in audited
            if item.quote in "你刚才提到故障风险很高。"
        ]
        self.assertTrue(copied_spans)
        self.assertTrue(
            all(item.disposition == "accepted" for item in copied_spans)
        )
        self.assertTrue(
            all(not item.introduced_by_ai for item in copied_spans)
        )

    def test_response_origin_uses_preceding_visible_turn_type(self) -> None:
        classify = EvidenceTrackerService.classify_response_origin
        self.assertEqual(
            classify(
                formal_answer=True,
                preceding_ai_content_type="interview_opening",
            ),
            "spontaneous_evidence",
        )
        self.assertEqual(
            classify(
                formal_answer=True,
                preceding_ai_content_type="interview_event",
            ),
            "spontaneous_evidence",
        )
        self.assertEqual(
            classify(
                formal_answer=True,
                preceding_ai_content_type="interview_followup",
            ),
            "elicited_evidence",
        )
        self.assertEqual(
            classify(
                formal_answer=False,
                preceding_ai_content_type="interview_followup",
            ),
            "not_scored",
        )
        self.assertEqual(
            classify(
                formal_answer=True,
                preceding_ai_content_type="interview_clarification",
            ),
            "elicited_evidence",
        )
        self.assertEqual(
            classify(
                formal_answer=False,
                preceding_ai_content_type="interview_clarification",
            ),
            "not_scored",
        )

    def test_preceding_ai_verbatim_copy_is_invalid_and_excluded(self) -> None:
        audited = EvidenceTrackerService.annotate_observations(
            [_observation("故障风险很高")],
            response_origin="elicited_evidence",
            source_turn_id=22,
            preceding_ai_turn_id=21,
            preceding_ai_text="你提到“故障风险很高”。你会如何判断？",
            earlier_user_texts=[],
        )
        item = audited[0]
        self.assertTrue(item.introduced_by_ai)
        self.assertEqual(item.validity, "invalid")
        self.assertEqual(item.disposition, "excluded")
        self.assertEqual(
            item.exclusion_reason,
            "introduced_verbatim_by_preceding_ai",
        )
        self.assertEqual(item.response_origin, "elicited_evidence")
        self.assertEqual(item.source_turn_id, 22)
        self.assertEqual(item.preceding_ai_turn_id, 21)
        self.assertEqual(item.evidence_policy_version, EVIDENCE_POLICY_VERSION)

    def test_earlier_user_wording_exempts_a_later_ai_reflection(self) -> None:
        audited = EvidenceTrackerService.annotate_observations(
            [_observation("故障风险很高")],
            response_origin="elicited_evidence",
            source_turn_id=24,
            preceding_ai_turn_id=23,
            preceding_ai_text="你之前说“故障风险很高”。接下来会怎么做？",
            earlier_user_texts=["我判断故障风险很高，所以先暂停。"],
        )
        item = audited[0]
        self.assertFalse(item.introduced_by_ai)
        self.assertEqual(item.validity, "valid")
        self.assertEqual(item.disposition, "accepted")
        self.assertIsNone(item.exclusion_reason)

    def test_mixed_answer_excludes_only_the_copied_observation(self) -> None:
        audited = EvidenceTrackerService.annotate_observations(
            [
                _observation("故障风险很高"),
                _observation(
                    "我会先小范围试用",
                    behavior_key="identify_gap_and_verification",
                ),
            ],
            response_origin="elicited_evidence",
            source_turn_id=32,
            preceding_ai_turn_id=31,
            preceding_ai_text="如果故障风险很高，你会怎么办？",
            earlier_user_texts=[],
        )
        self.assertEqual(
            [item.disposition for item in audited],
            ["excluded", "accepted"],
        )
        self.assertEqual(
            EvidenceTrackerService.original_span_after_exclusions(
                "故障风险很高；但我会先小范围试用。",
                [audited[0].quote],
            ),
            "但我会先小范围试用",
        )

    def test_only_copied_text_does_not_create_a_weak_bypass_span(self) -> None:
        self.assertIsNone(
            EvidenceTrackerService.original_span_after_exclusions(
                "故障风险很高",
                ["故障风险很高"],
            )
        )

    def test_tracker_defensively_refuses_excluded_observation(self) -> None:
        state = InterviewState(
            current_node_code="s2_evidence_verification",
            dimension_slots={
                "evidence_evaluation": DimensionSlotState(
                    dimension_key="evidence_evaluation",
                    status="not_started",
                    missing_behavior_keys=[
                        "inspect_source_sample_quality",
                        "distinguish_fact_opinion_assumption",
                        "identify_gap_and_verification",
                    ],
                )
            },
        )
        excluded = EvidenceTrackerService.annotate_observations(
            [_observation("故障风险很高")],
            response_origin="elicited_evidence",
            source_turn_id=42,
            preceding_ai_turn_id=41,
            preceding_ai_text="故障风险很高",
            earlier_user_texts=[],
        )
        deltas = EvidenceTrackerService().apply(
            state,
            turn_id=42,
            observations=excluded,
        )
        slot = state.dimension_slots["evidence_evaluation"]
        self.assertEqual(slot.status, "not_started")
        self.assertEqual(slot.observed_behavior_keys, [])
        self.assertEqual(slot.evidence_turn_ids, [])
        self.assertEqual(slot.diagnostic_low_evidence_turn_ids, [])
        self.assertEqual(deltas[0].status_before, "not_started")
        self.assertEqual(deltas[0].status_after, "not_started")
        self.assertIsNone(deltas[0].confidence_before)
        self.assertIsNone(deltas[0].confidence_after)
        self.assertEqual(
            state.evidence_timeline[-1]["observations"][0]["disposition"],
            "excluded",
        )

    def test_schema_rejects_unexplained_ai_introduced_evidence(self) -> None:
        with self.assertRaises(ValidationError):
            EvidenceObservation(
                dimension_key="evidence_evaluation",
                behavior_key="inspect_source_sample_quality",
                quote="故障风险很高",
                rationale="非法组合",
                extraction_confidence=0.8,
                introduced_by_ai=True,
            )

    def test_v11_authority_request_distinguishes_pure_and_mixed(self) -> None:
        pure = analyze_humanistic_authority_request(
            "你觉得我应该上线还是延期？"
        )
        self.assertIsNotNone(pure)
        self.assertEqual(pure.kind, "pure")
        self.assertIsNone(pure.substantive_text)

        mixed = analyze_humanistic_authority_request(
            "我倾向延期，因为故障风险高；但你觉得我应该上线还是延期？"
        )
        self.assertIsNotNone(mixed)
        self.assertEqual(mixed.kind, "mixed")
        self.assertEqual(
            mixed.substantive_text,
            "我倾向延期，因为故障风险高",
        )
        self.assertEqual(
            mixed.substantive_fragments,
            ("我倾向延期，因为故障风险高",),
        )
        authority_first = analyze_humanistic_authority_request(
            "你觉得我应该上线还是延期？我倾向延期，因为故障风险高。"
        )
        self.assertIsNotNone(authority_first)
        self.assertEqual(authority_first.kind, "mixed")
        self.assertEqual(
            authority_first.substantive_text,
            "我倾向延期，因为故障风险高",
        )
        self.assertIsNone(
            analyze_humanistic_authority_request(
                "我倾向延期，因为故障风险高。"
            )
        )

    def test_v11_authority_analyzer_is_decision_specific(self) -> None:
        for text in (
            "你能当我的心理咨询师吗？",
            "你能做我的朋友吗？",
            "你以前遇到这种事是怎么做的？",
            "请说说你的私人经历。",
            "我决定延期。",
        ):
            with self.subTest(text=text):
                self.assertIsNone(
                    analyze_humanistic_authority_request(text)
                )

        for text in (
            "帮我决定。",
            "帮我决定要不要上线。",
            "替我决定。",
            "帮我选一个。",
            "替我选。",
            "替我选择延期方案。",
            "直接给答案。",
            "给我一个答案。",
            "我该怎么办？",
            "直接告诉我选哪个？",
        ):
            with self.subTest(text=text):
                request = analyze_humanistic_authority_request(text)
                self.assertIsNotNone(request)
                self.assertEqual(request.kind, "pure")
                self.assertEqual(request.substantive_fragments, ())

    def test_v11_mixed_authority_preserves_all_substantive_fragments_in_order(
        self,
    ) -> None:
        request = analyze_humanistic_authority_request(
            "我倾向延期，因为故障风险高；"
            "你替我决定吧；"
            "我会先核实日志，再设置回滚阈值。"
        )

        self.assertIsNotNone(request)
        self.assertEqual(request.kind, "mixed")
        self.assertEqual(
            request.substantive_fragments,
            (
                "我倾向延期，因为故障风险高",
                "我会先核实日志，再设置回滚阈值",
            ),
        )
        self.assertEqual(
            request.substantive_text,
            "我倾向延期，因为故障风险高；"
            "我会先核实日志，再设置回滚阈值",
        )
        self.assertEqual(request.authority_spans, ("你替我决定吧",))

    def test_v11_mixed_authority_keeps_short_choice_comparisons_not_politeness(
        self,
    ) -> None:
        for text, expected in (
            ("A更稳妥；你替我选。", "A更稳妥"),
            ("我的首选是A。你替我决定。", "我的首选是A"),
        ):
            with self.subTest(text=text):
                request = analyze_humanistic_authority_request(text)
                self.assertIsNotNone(request)
                self.assertEqual(request.kind, "mixed")
                self.assertEqual(request.substantive_fragments, (expected,))
                self.assertEqual(request.substantive_text, expected)

        for text in (
            "麻烦你替我选，谢谢。",
            "拜托了，帮我决定好吗？",
        ):
            with self.subTest(text=text):
                request = analyze_humanistic_authority_request(text)
                self.assertIsNotNone(request)
                self.assertEqual(request.kind, "pure")
                self.assertEqual(request.substantive_fragments, ())

    def test_v11_style_is_selectable_only_behind_existing_flag(self) -> None:
        enabled = SimpleNamespace(
            INTERVIEWER_STYLE_ENABLED=True,
            INTERVIEWER_STYLE_DEFAULT=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )
        disabled = SimpleNamespace(
            INTERVIEWER_STYLE_ENABLED=False,
            INTERVIEWER_STYLE_DEFAULT=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )
        session = AssessmentSession(
            session_uuid="00000000-0000-0000-0000-000000000001",
            participant_id=1,
            scenario_id=1,
            selection_mode="test",
            status="in_progress",
            assessment_mode="mock",
            flow_version="progressive_v3_3",
            interviewer_style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )
        with patch(
            "app.services.session_service.get_settings",
            return_value=enabled,
        ):
            self.assertEqual(
                _default_interviewer_style(),
                HUMANISTIC_INTERVIEWER_STYLE_V1_1,
            )
            self.assertEqual(
                _applied_interviewer_style(session),
                HUMANISTIC_INTERVIEWER_STYLE_V1_1,
            )
        with patch(
            "app.services.session_service.get_settings",
            return_value=disabled,
        ):
            self.assertEqual(
                _default_interviewer_style(),
                BASELINE_INTERVIEWER_STYLE,
            )
            self.assertEqual(
                _applied_interviewer_style(session),
                BASELINE_INTERVIEWER_STYLE,
            )

    def test_admin_trace_sanitizer_keeps_v11_style(self) -> None:
        sanitized = _trace_audit_fields(
            {"interviewer_style_version": HUMANISTIC_INTERVIEWER_STYLE_V1_1}
        )
        self.assertEqual(
            sanitized["interviewer_style_version"],
            HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )


if __name__ == "__main__":
    unittest.main()

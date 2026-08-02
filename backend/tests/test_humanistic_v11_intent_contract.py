from __future__ import annotations

import unittest
from copy import deepcopy
from pathlib import Path

from app.agents.humanistic_evaluation_context import (
    build_runtime_context,
    load_context_manifest,
)
from app.agents.humanistic_interviewer_v11 import build_v11_microstructure
from app.agents.humanistic_v11_intent_registry import (
    INTENT_REGISTRY_VERSION,
    candidate_semantic_errors,
    resolve_intent_binding,
    semantic_binding_contract_errors,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
CONTEXT_MANIFEST = (
    BACKEND_ROOT
    / "tests"
    / "fixtures"
    / "humanistic_interviewer"
    / "pilot_context_manifest_v1.json"
)


class HumanisticV11IntentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.records = load_context_manifest(
            CONTEXT_MANIFEST,
            repo_root=REPO_ROOT,
            require_frozen=True,
        )
        cls.by_id = {item.context_id: item for item in cls.records}

    def test_all_frozen_contexts_have_explicit_semantic_bindings(self) -> None:
        self.assertEqual(len(self.records), 48)
        for record in self.records:
            with self.subTest(context_id=record.context_id):
                plan = record.frozen_plan
                payload = build_v11_microstructure(
                    build_runtime_context(record),
                    plan,
                    previous_questions=[],
                )
                self.assertEqual(
                    payload["intent_registry_version"],
                    INTENT_REGISTRY_VERSION,
                )
                self.assertEqual(
                    semantic_binding_contract_errors(plan, payload),
                    [],
                )
                if plan.action == "CONCLUDE":
                    self.assertEqual(payload["question_candidates"], [])
                    self.assertEqual(payload["selected_question"], "")
                    continue

                self.assertEqual(len(payload["question_candidates"]), 3)
                self.assertNotIn(
                    "runtime_dimension",
                    payload["candidate_mapping_source"],
                )
                self.assertEqual(
                    len(payload["candidate_mapping_fingerprint"]),
                    64,
                )
                self.assertTrue(
                    all(
                        item["eligible"]
                        and item["intent_family"] == payload["candidate_intent_key"]
                        and item["mapping_fingerprint"]
                        == payload["candidate_mapping_fingerprint"]
                        and item["semantic_contract_codes"] == []
                        for item in payload["question_candidates"]
                    )
                )

    def test_targeted_frozen_intents_keep_planner_semantics(self) -> None:
        expected = {
            "HIV1-E14": (
                "event_evidence_reassessment",
                (("证据", "补查", "核实"),),
            ),
            "HIV1-P06": (
                "decision_continue_stop_rules",
                (("继续",), ("停止",), ("标准", "条件", "结果")),
            ),
            "HIV1-P07": (
                "perspective_priority_impact",
                (("排序", "优先级"), ("影响",)),
            ),
            "HIV1-P09": (
                "reasoning_causal_comparison",
                (("因果",), ("比较", "对照")),
            ),
        }
        for context_id, (family_id, semantic_groups) in expected.items():
            with self.subTest(context_id=context_id):
                record = self.by_id[context_id]
                payload = build_v11_microstructure(
                    build_runtime_context(record),
                    record.frozen_plan,
                    previous_questions=[],
                )
                self.assertEqual(payload["candidate_intent_key"], family_id)
                for candidate in payload["question_candidates"]:
                    for group in semantic_groups:
                        self.assertTrue(
                            any(term in candidate["text"] for term in group),
                            candidate["text"],
                        )

    def test_clarify_and_repair_intents_use_specific_families(self) -> None:
        expected = {
            "HIV1-C01": "clarify_observable_metric",
            "HIV1-C02": "clarify_decision_threshold",
            "HIV1-C03": "clarify_sample_basis",
            "HIV1-C04": "clarify_scope_basis",
            "HIV1-C05": "clarify_agreement_basis",
            "HIV1-C07": "reasoning_condition_link",
            "HIV1-R01": "repair_evidence_criterion",
            "HIV1-R02": "repair_pilot_validation",
            "HIV1-R03": "repair_cross_validation",
            "HIV1-R04": "pure_authority_criteria",
            "HIV1-R05": "redirect_decision_criteria",
            "HIV1-R07": "redirect_observable_tradeoff",
        }
        for context_id, family_id in expected.items():
            with self.subTest(context_id=context_id):
                record = self.by_id[context_id]
                payload = build_v11_microstructure(
                    build_runtime_context(record),
                    record.frozen_plan,
                    previous_questions=[],
                )
                self.assertEqual(payload["candidate_intent_key"], family_id)
                self.assertTrue(
                    all(
                        item["intent_family"] == family_id
                        for item in payload["question_candidates"]
                    )
                )

    def test_runtime_special_intents_resolve_without_generic_semantic_drift(
        self,
    ) -> None:
        base = self.by_id["HIV1-P01"].frozen_plan
        cases = (
            (
                base.model_copy(
                    update={
                        "question_intent": ("在既有安排、减少检查或小范围试用中形成初步决定"),
                        "target_evidence": "形成反向信息前的明确初步决定",
                        "target_dimension": "integrative_decision",
                    }
                ),
                "decision_initial_choice",
            ),
            (
                base.model_copy(
                    update={
                        "question_intent": "为尚未充分的维度提供一次新的公平作答机会",
                        "target_evidence": "补足结束前仍不充分的维度证据",
                        "target_dimension": "reasoning_argumentation",
                    }
                ),
                "dimension_reasoning",
            ),
            (
                base.model_copy(
                    update={
                        "action": "CLARIFY",
                        "delivery_mode": "clarification",
                        "response_intent": "low_information",
                        "question_intent": "用更容易回答的方式澄清用户意思",
                        "target_dimension": None,
                        "target_evidence": None,
                    }
                ),
                "clarify_low_information",
            ),
            (
                base.model_copy(
                    update={
                        "action": "CLARIFY",
                        "delivery_mode": "clarification",
                        "response_intent": "conversation_repair",
                        "question_intent": ("承接纠错，改从dynamic_adjustment角度提出未重复问题"),
                        "target_dimension": "dynamic_adjustment",
                        "target_evidence": "从未重复的观察角度补充一项可判断信息",
                    }
                ),
                "repair_dimension_adjustment",
            ),
            (
                base.model_copy(
                    update={
                        "action": "INTEGRATE",
                        "delivery_mode": "integration",
                        "question_intent": "整合已谈到的依据、风险和行动安排",
                        "target_dimension": None,
                        "target_evidence": None,
                    }
                ),
                "integration_general",
            ),
        )
        for plan, family_id in cases:
            with self.subTest(family_id=family_id):
                binding = resolve_intent_binding(plan)
                self.assertIsNotNone(binding)
                assert binding is not None
                self.assertEqual(binding.family.family_id, family_id)
                self.assertEqual(len(binding.family.candidates), 3)

        for event_code, expected_family in {
            "evidence_uncertainty": "event_uncertainty_check",
            "stakeholder_conflict": "event_stakeholder_priority",
            "decision_pressure": "event_decision_under_constraint",
            "counter_evidence": "event_judgment_revision",
            "integration": "event_final_plan",
        }.items():
            with self.subTest(event_code=event_code):
                plan = base.model_copy(
                    update={
                        "action": "RELEASE_EVENT",
                        "delivery_mode": "event_link",
                        "question_intent": {
                            "evidence_uncertainty": ("结合新出现的不确定信息说明下一步核实重点"),
                            "stakeholder_conflict": ("比较新出现角色冲突中的优先考虑"),
                            "decision_pressure": "在约束下形成初步安排",
                            "counter_evidence": "说明新信息是否改变原判断及原因",
                            "integration": "形成最终可执行方案和调整条件",
                        }[event_code],
                        "target_dimension": None,
                        "target_evidence": None,
                        "release_event_code": event_code,
                        "release_unit_code": f"{event_code}_unit",
                    }
                )
                binding = resolve_intent_binding(plan)
                self.assertIsNotNone(binding)
                assert binding is not None
                self.assertEqual(binding.family.family_id, expected_family)
                self.assertEqual(
                    [
                        candidate_semantic_errors(
                            text,
                            binding=binding,
                            stable_order=index,
                        )
                        for index, text in enumerate(binding.family.candidates)
                    ],
                    [[], [], []],
                )

    def test_hard_contract_recomputes_registry_instead_of_trusting_labels(
        self,
    ) -> None:
        record = self.by_id["HIV1-P09"]
        payload = build_v11_microstructure(
            build_runtime_context(record),
            record.frozen_plan,
            previous_questions=[],
        )
        tampered = deepcopy(payload)
        tampered["question_candidates"][0]["text"] = "你准备先做哪一步？"
        tampered["question_candidates"][0]["semantic_contract_codes"] = []
        self.assertIn(
            "semantic_candidate_registry_mismatch",
            semantic_binding_contract_errors(record.frozen_plan, tampered),
        )

        wrong_source = deepcopy(payload)
        wrong_source["candidate_mapping_source"] = "self_claimed:same_family"
        self.assertIn(
            "candidate_mapping_source_mismatch",
            semantic_binding_contract_errors(record.frozen_plan, wrong_source),
        )


if __name__ == "__main__":
    unittest.main()

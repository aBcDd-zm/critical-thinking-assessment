from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from app.agents.humanistic_evaluation_context import HumanisticContextManifest


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / (
    "backend/tests/fixtures/humanistic_interviewer/"
    "pilot_context_manifest_v1.json"
)


class GenerationContractAmendmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Deliberately load only the manifest. The review-example asset is not
        # resolved or opened by this amendment gate.
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    def _assert_rejected(self, payload: dict, pattern: str) -> None:
        with self.assertRaisesRegex(ValidationError, pattern):
            HumanisticContextManifest.model_validate(payload)

    def test_valid_amendment_is_self_contained_and_accepted(self) -> None:
        parsed = HumanisticContextManifest.model_validate(self.manifest)

        self.assertIsNotNone(parsed.generation_contract_amendment)
        amendment = parsed.generation_contract_amendment
        assert amendment is not None
        self.assertEqual(
            amendment.review_examples_handling,
            "carried_forward_without_file_read",
        )
        self.assertFalse(amendment.candidate_generation_started)

    def test_am_rj_01_previous_manifest_sha_drift_is_rejected(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_contract_amendment"][
            "previous_manifest_sha256"
        ] = "0" * 64

        self._assert_rejected(payload, "previous manifest SHA-256 drift")

    def test_am_rj_02_context_change_is_rejected(self) -> None:
        mutations = (
            ("contexts_unchanged", False, "generation_contract_amendment"),
            (
                "development_contexts_sha256",
                "0" * 64,
                "generation amendment context SHA-256 drift",
            ),
            (
                "locked_test_contexts_sha256",
                "0" * 64,
                "generation amendment context SHA-256 drift",
            ),
        )
        for field, value, error_pattern in mutations:
            with self.subTest(field=field):
                payload = copy.deepcopy(self.manifest)
                payload["generation_contract_amendment"][field] = value
                self._assert_rejected(payload, error_pattern)

    def test_am_rj_03_review_carry_forward_drift_is_rejected(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_contract_amendment"][
            "review_examples_sha256"
        ] = "0" * 64
        self._assert_rejected(payload, "review-example SHA-256 drift")

        payload = copy.deepcopy(self.manifest)
        payload["generation_contract_amendment"][
            "review_examples_handling"
        ] = "read_and_rehashed"
        self._assert_rejected(payload, "review_examples_handling")

    def test_am_rj_04_approved_contract_changes_are_exact(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_contract_amendment"]["approved_change_ids"][0] = (
            "UNAPPROVED"
        )

        self._assert_rejected(payload, "approved change IDs are incomplete")

    def test_am_rj_05_amendment_gate_ids_are_exact(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_contract_amendment"][
            "approved_amendment_gate_ids"
        ][0] = "AMEND-Z"

        self._assert_rejected(payload, "approve AMEND-A through AMEND-H")

    def test_am_rj_06_rejection_test_ids_are_exact(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_contract_amendment"][
            "approved_rejection_test_ids"
        ][0] = "AM-RJ-99"

        self._assert_rejected(payload, "rejection-test IDs are incomplete")

    def test_am_rj_07_output_contract_lock_drift_is_rejected(self) -> None:
        mutations = (
            ("output_contract_version", "interviewer_output_contract_v0"),
            ("output_contract_sha256", "0" * 64),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                payload = copy.deepcopy(self.manifest)
                payload["generation_contract_amendment"][field] = value
                self._assert_rejected(payload, "output contract")

    def test_am_rj_08_smoke_evidence_drift_is_rejected(self) -> None:
        mutations = (
            ("smoke_preflight_sha256", "0" * 64),
            ("smoke_audit_sha256", "0" * 64),
            ("smoke_status", "blocked"),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                payload = copy.deepcopy(self.manifest)
                payload["generation_contract_amendment"][field] = value
                self._assert_rejected(payload, "smoke")

    def test_am_rj_09_artifact_change_set_and_hashes_are_exact(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_contract_amendment"]["artifact_changes"][0][
            "artifact_id"
        ] = "unapproved_artifact"
        self._assert_rejected(payload, "artifact changes are not minimal")

        payload = copy.deepcopy(self.manifest)
        payload["generation_contract_amendment"]["artifact_changes"][0][
            "current_sha256"
        ] = "0" * 64
        self._assert_rejected(payload, "artifact history is not linked")

    def test_am_rj_10_generation_started_is_rejected(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_contract_amendment"][
            "candidate_generation_started"
        ] = True

        self._assert_rejected(payload, "candidate_generation_started")

    def test_reliability_amendment_records_explicit_scope(self) -> None:
        parsed = HumanisticContextManifest.model_validate(self.manifest)
        amendment = parsed.generation_reliability_amendment
        self.assertIsNotNone(amendment)
        assert amendment is not None
        self.assertEqual(amendment.candidate_timeout_seconds, 15)
        self.assertEqual(
            set(amendment.applies_identically_to_arms),
            {"baseline", "humanistic"},
        )
        self.assertTrue(amendment.generation_restart_authorized)

    def test_reliability_previous_freeze_locks_are_exact(self) -> None:
        for field in ("previous_manifest_sha256", "previous_preflight_sha256"):
            with self.subTest(field=field):
                payload = copy.deepcopy(self.manifest)
                payload["generation_reliability_amendment"][field] = "0" * 64
                self._assert_rejected(payload, "generation reliability previous")

    def test_reliability_blocked_audit_chain_is_exact(self) -> None:
        fields = (
            "triggering_run_id",
            "triggering_manifest_sha256",
            "triggering_provenance_sha256",
            "triggering_failures_sha256",
        )
        for field in fields:
            with self.subTest(field=field):
                payload = copy.deepcopy(self.manifest)
                payload["generation_reliability_amendment"][field] = (
                    "run_" + "0" * 32 if field == "triggering_run_id" else "0" * 64
                )
                self._assert_rejected(payload, "generation reliability blocked")

    def test_reliability_scope_cannot_change_measurement_assets(self) -> None:
        fields = (
            "contexts_unchanged",
            "style_policy_unchanged",
            "validator_unchanged",
            "scoring_unchanged",
            "output_contract_unchanged",
            "shared_event_structure_constraint",
        )
        for field in fields:
            with self.subTest(field=field):
                payload = copy.deepcopy(self.manifest)
                payload["generation_reliability_amendment"][field] = False
                self._assert_rejected(payload, field)

    def test_reliability_timeout_and_arm_fairness_are_exact(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment"][
            "candidate_timeout_seconds"
        ] = 14
        self._assert_rejected(payload, "candidate_timeout_seconds")

        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment"][
            "applies_identically_to_arms"
        ] = ["baseline", "baseline"]
        self._assert_rejected(payload, "apply identically to both arms")

    def test_reliability_artifact_changes_are_minimal_and_linked(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment"]["artifact_changes"][0][
            "artifact_id"
        ] = "unapproved_artifact"
        self._assert_rejected(
            payload,
            "generation reliability artifact changes are not minimal",
        )

        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment"]["artifact_changes"][0][
            "current_sha256"
        ] = "0" * 64
        self._assert_rejected(
            payload,
            "generation reliability artifact history is not linked",
        )

    def test_reliability_event_smoke_locks_are_exact(self) -> None:
        for field in (
            "event_smoke_preflight_sha256",
            "event_smoke_audit_sha256",
        ):
            with self.subTest(field=field):
                payload = copy.deepcopy(self.manifest)
                payload["generation_reliability_amendment"][field] = "0" * 64
                self._assert_rejected(payload, "generation reliability event smoke")

    def test_reliability_restart_must_be_authorized_and_not_started(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment"][
            "generation_restart_authorized"
        ] = False
        self._assert_rejected(payload, "generation_restart_authorized")

        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment"][
            "formal_candidate_generation_started_after_amendment"
        ] = True
        self._assert_rejected(
            payload,
            "formal_candidate_generation_started_after_amendment",
        )

    def test_reliability_v2_records_cross_round_policy_and_matrix(self) -> None:
        parsed = HumanisticContextManifest.model_validate(self.manifest)
        amendment = parsed.generation_reliability_amendment_v2
        self.assertIsNotNone(amendment)
        assert amendment is not None
        self.assertEqual(
            amendment.retry_selection_policy,
            "first_valid_per_arm_across_paired_rounds",
        )
        self.assertTrue(amendment.paired_arm_calls_remain_symmetric)
        self.assertEqual(amendment.action_matrix_remote_call_count, 12)

    def test_v3_frozen_record_contains_complete_smoke_evidence(self) -> None:
        parsed = HumanisticContextManifest.model_validate(self.manifest)
        amendment = parsed.generation_reliability_amendment_v3
        self.assertIsNotNone(amendment)
        assert amendment is not None
        self.assertEqual(amendment.status, "frozen_after_smoke")
        self.assertEqual(amendment.action_matrix_status, "pass")
        self.assertEqual(amendment.action_matrix_remote_call_count, 12)
        self.assertIsNotNone(amendment.action_matrix_audit_sha256)
        self.assertTrue(amendment.generation_restart_authorized)

    def test_v3_rj_01_gate_ids_are_exact(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v3"][
            "approved_gate_ids"
        ][0] = "V3-Z"
        self._assert_rejected(payload, "requires V3-A through V3-I")

    def test_v3_rj_02_rejection_ids_are_exact(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v3"][
            "approved_rejection_test_ids"
        ][0] = "V3-RJ-99"
        self._assert_rejected(payload, "rejection-test IDs are incomplete")

    def test_v3_rj_03_previous_freeze_chain_is_exact(self) -> None:
        for field in ("previous_manifest_sha256", "previous_preflight_sha256"):
            with self.subTest(field=field):
                payload = copy.deepcopy(self.manifest)
                payload["generation_reliability_amendment_v3"][field] = "0" * 64
                self._assert_rejected(payload, "generation reliability v3")

    def test_v3_rj_04_trigger_and_corrective_evidence_are_exact(self) -> None:
        fields = (
            "triggering_manifest_sha256",
            "triggering_provenance_sha256",
            "triggering_failures_sha256",
            "corrective_interviewer_sha256",
            "corrective_action_matrix_preflight_sha256",
            "corrective_action_matrix_audit_sha256",
        )
        for field in fields:
            with self.subTest(field=field):
                payload = copy.deepcopy(self.manifest)
                payload["generation_reliability_amendment_v3"][field] = "0" * 64
                self._assert_rejected(payload, "generation reliability v3")

    def test_v3_rj_05_context_scope_cannot_change(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v3"][
            "contexts_unchanged"
        ] = False
        self._assert_rejected(payload, "Input should be True")

    def test_v3_rj_06_internal_term_source_is_fixed(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v3"][
            "internal_terms_source"
        ] = "duplicated_prompt_list"
        self._assert_rejected(payload, "InterviewQuestionValidator.INTERNAL_TERMS")

    def test_v3_rj_07_arm_symmetry_cannot_change(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v3"][
            "shared_constraint_identical_for_model_arms"
        ] = False
        self._assert_rejected(payload, "Input should be True")

    def test_v3_rj_08_quality_flags_remain_non_authoritative(self) -> None:
        for field in (
            "quality_flags_non_authoritative",
            "quality_flag_mismatches_audit_only",
        ):
            with self.subTest(field=field):
                payload = copy.deepcopy(self.manifest)
                payload["generation_reliability_amendment_v3"][field] = False
                self._assert_rejected(payload, "Input should be True")

    def test_v3_rj_09_artifact_change_set_is_minimal(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v3"][
            "artifact_changes"
        ][0]["artifact_id"] = "prompt_seed_registry"
        self._assert_rejected(payload, "artifact changes are not minimal")

    def test_v3_rj_10_artifact_history_is_linked(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v3"][
            "artifact_changes"
        ][0]["previous_sha256"] = "0" * 64
        self._assert_rejected(payload, "previous artifact SHA-256 drift")

    def test_v3_rj_11_provisional_state_cannot_claim_pass(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v3"][
            "status"
        ] = "provisional_before_smoke"
        self._assert_rejected(payload, "provisional state cannot claim pass")

    def test_v3_rj_12_frozen_state_requires_complete_smoke_evidence(self) -> None:
        payload = copy.deepcopy(self.manifest)
        amendment = payload["generation_reliability_amendment_v3"]
        amendment["action_matrix_audit_sha256"] = None
        self._assert_rejected(
            payload,
            "frozen state requires complete smoke evidence",
        )

    def test_reliability_v2_previous_and_blocked_locks_are_exact(self) -> None:
        fields = (
            "previous_manifest_sha256",
            "previous_preflight_sha256",
            "triggering_manifest_sha256",
            "triggering_provenance_sha256",
            "triggering_failures_sha256",
        )
        for field in fields:
            with self.subTest(field=field):
                payload = copy.deepcopy(self.manifest)
                payload["generation_reliability_amendment_v2"][field] = "0" * 64
                self._assert_rejected(payload, "generation reliability v2")

    def test_reliability_v2_action_mapping_and_retry_are_exact(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v2"][
            "approved_plan_actions"
        ][0] = "UNAPPROVED"
        self._assert_rejected(payload, "cover all six plan actions")

        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v2"][
            "retry_selection_policy"
        ] = "same_round_only"
        self._assert_rejected(payload, "retry_selection_policy")

    def test_reliability_v2_artifact_chain_is_exact(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v2"][
            "artifact_changes"
        ][0]["artifact_id"] = "unapproved_artifact"
        self._assert_rejected(
            payload,
            "generation reliability v2 artifact changes are not minimal",
        )

        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v2"][
            "artifact_changes"
        ][0]["current_sha256"] = "0" * 64
        self._assert_rejected(
            payload,
            "generation reliability v2 current artifact SHA-256 drift",
        )

    def test_reliability_v2_matrix_evidence_is_exact(self) -> None:
        for field in (
            "action_matrix_preflight_sha256",
            "action_matrix_audit_sha256",
        ):
            with self.subTest(field=field):
                payload = copy.deepcopy(self.manifest)
                payload["generation_reliability_amendment_v2"][field] = "0" * 64
                self._assert_rejected(payload, "generation reliability v2 matrix")

    def test_reliability_v2_restart_must_remain_unstarted(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v2"][
            "formal_candidate_generation_started_after_amendment"
        ] = True
        self._assert_rejected(
            payload,
            "formal_candidate_generation_started_after_amendment",
        )

    def test_v4_frozen_record_contains_complete_smoke_evidence(
        self,
    ) -> None:
        parsed = HumanisticContextManifest.model_validate(self.manifest)
        amendment = parsed.generation_reliability_amendment_v4
        self.assertIsNotNone(amendment)
        assert amendment is not None
        self.assertEqual(amendment.status, "frozen_after_smoke")
        self.assertEqual(amendment.action_matrix_status, "pass")
        self.assertEqual(amendment.event_smoke_status, "pass")
        self.assertEqual(amendment.action_matrix_remote_call_count, 12)
        self.assertEqual(amendment.event_smoke_remote_call_count, 6)
        self.assertEqual(len(amendment.event_smoke_audit_sha256), 3)
        self.assertTrue(amendment.generation_restart_authorized)
        self.assertEqual(
            parsed.candidate_generator_status,
            "pending_before_generation",
        )

    def test_v4_rj_01_gate_ids_are_exact(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v4"][
            "approved_gate_ids"
        ][0] = "V4-Z"
        self._assert_rejected(payload, "requires V4-A through V4-H")

    def test_v4_rj_02_rejection_ids_are_exact(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v4"][
            "approved_rejection_test_ids"
        ][0] = "V4-RJ-99"
        self._assert_rejected(payload, "rejection-test IDs are incomplete")

    def test_v4_rj_03_freeze_and_preflight_chain_is_exact(self) -> None:
        fields = (
            "previous_manifest_sha256",
            "previous_preflight_sha256",
            "action_matrix_preflight_sha256",
            "event_smoke_preflight_sha256",
        )
        for field in fields:
            with self.subTest(field=field):
                payload = copy.deepcopy(self.manifest)
                payload["generation_reliability_amendment_v4"][field] = "0" * 64
                self._assert_rejected(payload, "generation reliability v4")

    def test_v4_rj_04_triggering_evidence_is_exact(self) -> None:
        fields = (
            "triggering_manifest_sha256",
            "triggering_provenance_sha256",
            "triggering_failures_sha256",
        )
        for field in fields:
            with self.subTest(field=field):
                payload = copy.deepcopy(self.manifest)
                payload["generation_reliability_amendment_v4"][field] = "0" * 64
                self._assert_rejected(payload, "generation reliability v4 blocked")

    def test_v4_rj_05_frozen_data_scope_cannot_change(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v4"][
            "contexts_unchanged"
        ] = False
        self._assert_rejected(payload, "Input should be True")

    def test_v4_rj_06_constraint_is_release_event_candidate_only(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v4"][
            "applies_only_to_release_event_candidate_and_smoke"
        ] = False
        self._assert_rejected(payload, "Input should be True")

    def test_v4_rj_07_arm_and_retry_fairness_cannot_change(self) -> None:
        for field in (
            "shared_constraint_identical_for_model_arms",
            "paired_arm_calls_remain_symmetric",
        ):
            with self.subTest(field=field):
                payload = copy.deepcopy(self.manifest)
                payload["generation_reliability_amendment_v4"][field] = False
                self._assert_rejected(payload, "Input should be True")

    def test_v4_rj_08_terminal_budget_is_exact(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v4"][
            "assistant_authored_question_mark_count"
        ] = 2
        self._assert_rejected(payload, "Input should be 1")

        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v4"][
            "assistant_authored_other_terminal_max"
        ] = 2
        self._assert_rejected(payload, "Input should be 1")

    def test_v4_rj_09_no_normalization_or_event_fact_drift(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v4"][
            "model_output_normalization_enabled"
        ] = True
        self._assert_rejected(payload, "Input should be False")

        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v4"][
            "exact_event_unit_text_required"
        ] = False
        self._assert_rejected(payload, "Input should be True")

    def test_v4_rj_10_artifact_set_and_history_are_exact(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v4"][
            "artifact_changes"
        ][0]["artifact_id"] = "prompt_seed_registry"
        self._assert_rejected(payload, "artifact changes are not minimal")

        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v4"][
            "artifact_changes"
        ][0]["previous_sha256"] = "0" * 64
        self._assert_rejected(payload, "previous artifact SHA-256 drift")

    def test_v4_rj_11_provisional_state_cannot_claim_pass(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v4"][
            "status"
        ] = "provisional_before_smoke"
        self._assert_rejected(payload, "provisional state cannot claim pass")

    def test_v4_rj_12_frozen_manifest_must_enable_preflight(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["candidate_generator_status"] = "blocked_before_v4_smoke"
        self._assert_rejected(payload, "v5 frozen manifest must enable preflight")

    def test_v5_frozen_record_passes_zero_call_gate_and_enables_one_restart(
        self,
    ) -> None:
        parsed = HumanisticContextManifest.model_validate(self.manifest)
        amendment = parsed.generation_reliability_amendment_v5
        self.assertIsNotNone(amendment)
        assert amendment is not None
        self.assertEqual(amendment.status, "frozen_after_zero_call_gate")
        self.assertEqual(amendment.zero_call_remote_model_call_count, 0)
        self.assertEqual(amendment.zero_call_regression_status, "pass")
        self.assertEqual(
            amendment.formal_preflight_status,
            "ready",
        )
        self.assertTrue(amendment.generation_restart_authorized)
        self.assertFalse(amendment.automatic_v6_allowed)
        self.assertEqual(
            parsed.candidate_generator_status,
            "pending_before_generation",
        )

    def test_v5_rj_01_previous_freeze_chain_is_exact(self) -> None:
        for field in ("previous_manifest_sha256", "previous_preflight_sha256"):
            with self.subTest(field=field):
                payload = copy.deepcopy(self.manifest)
                payload["generation_reliability_amendment_v5"][field] = "0" * 64
                self._assert_rejected(payload, "generation reliability v5")

    def test_v5_rj_02_blocked_evidence_chain_is_exact(self) -> None:
        fields = (
            "triggering_manifest_sha256",
            "triggering_provenance_sha256",
            "triggering_case_key_sha256",
            "triggering_failures_sha256",
        )
        for field in fields:
            with self.subTest(field=field):
                payload = copy.deepcopy(self.manifest)
                payload["generation_reliability_amendment_v5"][field] = "0" * 64
                self._assert_rejected(payload, "generation reliability v5 blocked")

    def test_v5_rj_11_frozen_assets_and_artifact_set_cannot_drift(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v5"][
            "prompt_registry_unchanged"
        ] = False
        self._assert_rejected(payload, "Input should be True")

        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v5"][
            "artifact_changes"
        ][0]["artifact_id"] = "prompt_seed_registry"
        self._assert_rejected(payload, "artifact changes are not minimal")

    def test_v5_rj_12_final_restart_and_automatic_v6_gate_are_exact(self) -> None:
        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v5"][
            "generation_restart_authorized"
        ] = False
        self._assert_rejected(payload, "frozen state requires ready preflight")

        payload = copy.deepcopy(self.manifest)
        payload["generation_reliability_amendment_v5"][
            "automatic_v6_allowed"
        ] = True
        self._assert_rejected(payload, "Input should be False")


if __name__ == "__main__":
    unittest.main()

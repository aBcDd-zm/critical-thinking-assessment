from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.evaluate_humanistic_interviewer_v1 import (
    DEFAULT_CONTEXTS_PATH,
    evaluate_release_gate,
)


class HumanisticInterviewerEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir_context = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self.temp_dir_context.name)
        self.contexts = [
            {
                "context_id": "CTX-01",
                "split": "train",
                "status": "synthetic_for_unit_test",
            },
            {
                "context_id": "CTX-02",
                "split": "locked_test",
                "status": "synthetic_for_unit_test",
            },
        ]
        self.paths = {
            name: self.temp_dir / f"{name}.jsonl"
            for name in (
                "contexts",
                "candidates",
                "ratings",
                "arm_key",
                "runtime",
                "uat",
            )
        }

    def tearDown(self) -> None:
        self.temp_dir_context.cleanup()

    @staticmethod
    def _write_jsonl(path: Path, records: list[dict]) -> None:
        path.write_text(
            "".join(
                json.dumps(record, ensure_ascii=False) + "\n"
                for record in records
            ),
            encoding="utf-8",
        )

    def _write_complete_evidence(self) -> None:
        self._write_jsonl(self.paths["contexts"], self.contexts)
        candidates = []
        arm_key = []
        ratings = []
        for context in self.contexts:
            context_id = context["context_id"]
            candidate_ids = [
                f"{context_id}-C1",
                f"{context_id}-C2",
                f"{context_id}-C3",
            ]
            candidates.append(
                {
                    "context_id": context_id,
                    "candidates": [
                        {
                            "candidate_id": candidate_id,
                            "candidate_text": f"仅用于算法单测的候选 {index}",
                        }
                        for index, candidate_id in enumerate(
                            candidate_ids, start=1
                        )
                    ],
                }
            )
            arm_key.append(
                {
                    "context_id": context_id,
                    "assignments": [
                        {"candidate_id": candidate_ids[0], "arm": "baseline"},
                        {
                            "candidate_id": candidate_ids[1],
                            "arm": "humanistic",
                        },
                        {"candidate_id": candidate_ids[2], "arm": "fallback"},
                    ],
                }
            )
            for reviewer_id in ("REVIEWER-X", "REVIEWER-Y"):
                ratings.append(
                    {
                        "context_id": context_id,
                        "reviewer_id": reviewer_id,
                        "candidate_ratings": [
                            {
                                "candidate_id": candidate_id,
                                "naturalness": 4,
                                "warmth": 4,
                                "clarity": 5,
                                "faithfulness_pass": True,
                                "non_leading_pass": True,
                                "single_question_pass": True,
                                "fact_whitelist_pass": True,
                                "reflection_basis_pass": True,
                                "hard_error_codes": [],
                            }
                            for candidate_id in candidate_ids
                        ],
                        "baseline_humanistic_preference": candidate_ids[1],
                    }
                )
        runtime = [
            {
                "record_id": f"RUN-{index}",
                "context_id": context["context_id"],
                "total_latency_ms": 8500 + index * 100,
                "renderer_fallback": False,
                "validation_codes": [],
                "hard_error_codes": [],
            }
            for index, context in enumerate(self.contexts, start=1)
        ]
        uat = [
            {
                "uat_run_id": f"UAT-{index:02d}",
                "tester_id": f"TESTER-{(index % 2) + 1}",
                "evidence_ref": f"internal://uat/{index:02d}",
                "completed": True,
                "outcome": "pass",
                "open_critical_issue": False,
            }
            for index in range(1, 11)
        ]
        self._write_jsonl(self.paths["candidates"], candidates)
        self._write_jsonl(self.paths["arm_key"], arm_key)
        self._write_jsonl(self.paths["ratings"], ratings)
        self._write_jsonl(self.paths["runtime"], runtime)
        self._write_jsonl(self.paths["uat"], uat)

    def _evaluate(self) -> dict:
        return evaluate_release_gate(
            contexts_path=self.paths["contexts"],
            candidate_packet_path=self.paths["candidates"],
            ratings_path=self.paths["ratings"],
            arm_key_path=self.paths["arm_key"],
            runtime_records_path=self.paths["runtime"],
            uat_records_path=self.paths["uat"],
            enforce_context_contract=False,
        )

    def test_complete_evidence_can_pass_all_gates(self) -> None:
        self._write_complete_evidence()

        report = self._evaluate()

        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["metrics"]["candidate_count"], 6)
        self.assertEqual(report["metrics"]["independent_review_count"], 4)
        self.assertEqual(report["metrics"]["humanistic_preference_rate"], 1.0)
        self.assertEqual(report["metrics"]["renderer_fallback_rate"], 0.0)
        self.assertEqual(report["metrics"]["production_hard_error_count"], 0)
        self.assertTrue(
            all(
                count == 0
                for count in report["metrics"][
                    "hard_error_counts_by_code"
                ].values()
            )
        )
        self.assertTrue(all(gate["passed"] for gate in report["gates"]))

    def test_completed_but_below_threshold_evidence_fails(self) -> None:
        self._write_complete_evidence()
        ratings = [
            json.loads(line)
            for line in self.paths["ratings"].read_text(encoding="utf-8").splitlines()
        ]
        for review in ratings:
            humanistic_rating = review["candidate_ratings"][1]
            humanistic_rating["warmth"] = 2
            humanistic_rating["hard_error_codes"] = [
                "prescriptive_authority"
            ]
            review["baseline_humanistic_preference"] = review[
                "candidate_ratings"
            ][0]["candidate_id"]
        self._write_jsonl(self.paths["ratings"], ratings)

        report = self._evaluate()

        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["metrics"]["humanistic_preference_rate"], 0.0)
        self.assertEqual(report["metrics"]["production_hard_error_count"], 4)
        self.assertEqual(
            report["metrics"]["hard_error_counts_by_code"][
                "prescriptive_authority"
            ],
            4,
        )
        failed_gate_ids = {
            gate["gate_id"] for gate in report["gates"] if not gate["passed"]
        }
        self.assertIn("warmth_mean", failed_gate_ids)
        self.assertIn("production_hard_error_count", failed_gate_ids)
        self.assertIn("locked_test_hard_error_count", failed_gate_ids)
        self.assertIn("humanistic_preference_rate", failed_gate_ids)

    def test_frozen_manifest_without_real_evidence_remains_blocked(
        self,
    ) -> None:
        report = evaluate_release_gate(contexts_path=DEFAULT_CONTEXTS_PATH)

        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["metrics"]["context_count"], 48)
        self.assertIn("candidate_packet: evidence not supplied", report["blockers"])
        self.assertEqual(report["gates"], [])

    def test_packet_with_not_exactly_three_candidates_is_blocked(self) -> None:
        self._write_complete_evidence()
        candidates = [
            json.loads(line)
            for line in self.paths["candidates"]
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        candidates[0]["candidates"].pop()
        self._write_jsonl(self.paths["candidates"], candidates)

        report = self._evaluate()

        self.assertEqual(report["status"], "BLOCKED")
        self.assertTrue(
            any(
                "exactly 3 candidates are required" in blocker
                for blocker in report["blockers"]
            )
        )

    def test_one_reviewer_per_context_is_blocked(self) -> None:
        self._write_complete_evidence()
        ratings = [
            json.loads(line)
            for line in self.paths["ratings"].read_text(encoding="utf-8").splitlines()
        ]
        ratings = [
            rating
            for rating in ratings
            if rating["reviewer_id"] == "REVIEWER-X"
        ]
        self._write_jsonl(self.paths["ratings"], ratings)

        report = self._evaluate()

        self.assertEqual(report["status"], "BLOCKED")
        self.assertTrue(
            any(
                "at least 2 independent reviewers" in blocker
                for blocker in report["blockers"]
            )
        )

    def test_blind_packet_rejects_arm_leakage(self) -> None:
        self._write_complete_evidence()
        candidates = [
            json.loads(line)
            for line in self.paths["candidates"]
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        candidates[0]["candidates"][0]["arm"] = "baseline"
        self._write_jsonl(self.paths["candidates"], candidates)

        report = self._evaluate()

        self.assertEqual(report["status"], "BLOCKED")
        self.assertTrue(
            any(
                "blind packet contains forbidden field" in blocker
                for blocker in report["blockers"]
            )
        )

    def test_exact_model_pair_tie_receives_neutral_preference_weight(self) -> None:
        self._write_complete_evidence()
        candidates = [
            json.loads(line)
            for line in self.paths["candidates"].read_text(encoding="utf-8").splitlines()
        ]
        tied_context_id = self.contexts[0]["context_id"]
        tied_text = candidates[0]["candidates"][0]["candidate_text"]
        candidates[0]["candidates"][1]["candidate_text"] = tied_text
        self._write_jsonl(self.paths["candidates"], candidates)
        ratings = [
            json.loads(line)
            for line in self.paths["ratings"].read_text(encoding="utf-8").splitlines()
        ]
        for review in ratings:
            if review["context_id"] == tied_context_id:
                review["baseline_humanistic_preference"] = review[
                    "candidate_ratings"
                ][0]["candidate_id"]
        self._write_jsonl(self.paths["ratings"], ratings)

        report = self._evaluate()

        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["metrics"]["exact_model_tie_context_count"], 1)
        self.assertEqual(report["metrics"]["exact_model_tie_review_count"], 2)
        self.assertEqual(report["metrics"]["humanistic_preference_rate"], 0.75)

    def test_v5_rj_10_exact_tie_with_inconsistent_ratings_is_blocked(self) -> None:
        self._write_complete_evidence()
        candidates = [
            json.loads(line)
            for line in self.paths["candidates"].read_text(encoding="utf-8").splitlines()
        ]
        candidates[0]["candidates"][1]["candidate_text"] = candidates[0][
            "candidates"
        ][0]["candidate_text"]
        self._write_jsonl(self.paths["candidates"], candidates)
        ratings = [
            json.loads(line)
            for line in self.paths["ratings"].read_text(encoding="utf-8").splitlines()
        ]
        ratings[0]["candidate_ratings"][1]["warmth"] = 3
        self._write_jsonl(self.paths["ratings"], ratings)

        report = self._evaluate()

        self.assertEqual(report["status"], "BLOCKED")
        self.assertTrue(
            any("identical ratings" in item for item in report["blockers"])
        )

    def test_fallback_or_normalized_only_collision_is_blocked(self) -> None:
        self._write_complete_evidence()
        candidates = [
            json.loads(line)
            for line in self.paths["candidates"].read_text(encoding="utf-8").splitlines()
        ]
        candidates[0]["candidates"][2]["candidate_text"] = candidates[0][
            "candidates"
        ][0]["candidate_text"]
        self._write_jsonl(self.paths["candidates"], candidates)
        report = self._evaluate()
        self.assertEqual(report["status"], "BLOCKED")
        self.assertTrue(
            any("baseline and humanistic" in item for item in report["blockers"])
        )

        self._write_complete_evidence()
        candidates = [
            json.loads(line)
            for line in self.paths["candidates"].read_text(encoding="utf-8").splitlines()
        ]
        candidates[0]["candidates"][1]["candidate_text"] = (
            candidates[0]["candidates"][0]["candidate_text"] + "！"
        )
        self._write_jsonl(self.paths["candidates"], candidates)
        report = self._evaluate()
        self.assertEqual(report["status"], "BLOCKED")
        self.assertTrue(
            any("normalized-only" in item for item in report["blockers"])
        )

    def test_release_contract_rejects_raw_jsonl_manifest_bypass(self) -> None:
        self._write_jsonl(self.paths["contexts"], self.contexts)

        report = evaluate_release_gate(contexts_path=self.paths["contexts"])

        self.assertEqual(report["status"], "BLOCKED")
        self.assertTrue(
            any(
                "raw JSONL is allowed only in internal unit tests" in blocker
                for blocker in report["blockers"]
            )
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import json
import stat
import tempfile
import unittest
from pathlib import Path

from scripts.analyze_humanistic_inter_rater_agreement_v1 import (
    AgreementInputError,
    analyze_inter_rater_agreement,
    main,
)


class HumanisticInterRaterAgreementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir_context = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self.temp_dir_context.name)
        self.ratings_path = self.temp_dir / "blind_ratings.jsonl"
        self.candidate_packet_path = (
            self.temp_dir / "candidate_packet.jsonl"
        )
        self._write_candidate_packet()

    def tearDown(self) -> None:
        self.temp_dir_context.cleanup()

    def _write_ratings(self, records: list[dict]) -> None:
        self.ratings_path.write_text(
            "".join(
                json.dumps(record, ensure_ascii=False) + "\n"
                for record in records
            ),
            encoding="utf-8",
        )

    def _write_candidate_packet(
        self,
        *,
        context_ids: tuple[str, ...] = ("CTX-01", "CTX-02"),
        exact_tie_contexts: frozenset[str] = frozenset(),
    ) -> None:
        records = []
        for context_id in context_ids:
            candidate_texts = [
                f"{context_id} candidate 1",
                f"{context_id} candidate 2",
                f"{context_id} candidate 3",
            ]
            if context_id in exact_tie_contexts:
                candidate_texts[1] = candidate_texts[0]
            records.append(
                {
                    "context_id": context_id,
                    "candidates": [
                        {
                            "candidate_id": f"{context_id}-C{index}",
                            "candidate_text": candidate_text,
                        }
                        for index, candidate_text in enumerate(
                            candidate_texts,
                            start=1,
                        )
                    ],
                }
            )
        self.candidate_packet_path.write_text(
            "".join(
                json.dumps(record, ensure_ascii=False) + "\n"
                for record in records
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _review(
        context_id: str,
        reviewer_id: str,
        *,
        naturalness: tuple[int, int, int] = (3, 4, 5),
        faithfulness: tuple[bool, bool, bool] = (True, False, True),
        preference_index: int = 0,
    ) -> dict:
        candidate_ids = [
            f"{context_id}-C1",
            f"{context_id}-C2",
            f"{context_id}-C3",
        ]
        return {
            "context_id": context_id,
            "reviewer_id": reviewer_id,
            "candidate_ratings": [
                {
                    "candidate_id": candidate_id,
                    "naturalness": naturalness[index],
                    "warmth": (5, 4, 3)[index],
                    "clarity": (4, 5, 3)[index],
                    "faithfulness_pass": faithfulness[index],
                    "non_leading_pass": (True, True, False)[index],
                    "single_question_pass": (True, False, True)[index],
                    "fact_whitelist_pass": (False, True, True)[index],
                    "reflection_basis_pass": (True, False, False)[index],
                    "hard_error_codes": [],
                }
                for index, candidate_id in enumerate(candidate_ids)
            ],
            "baseline_humanistic_preference": candidate_ids[
                preference_index
            ],
        }

    def _perfect_records(
        self,
        reviewers: tuple[str, ...] = ("REVIEWER-X", "REVIEWER-Y"),
    ) -> list[dict]:
        records = []
        for context_index, context_id in enumerate(("CTX-01", "CTX-02")):
            for reviewer_id in reviewers:
                records.append(
                    self._review(
                        context_id,
                        reviewer_id,
                        preference_index=context_index,
                    )
                )
        return records

    def test_reports_perfect_pairwise_agreement_without_arm_key(self) -> None:
        self._write_ratings(self._perfect_records())

        report = analyze_inter_rater_agreement(
            ratings_path=self.ratings_path,
            candidate_packet_path=self.candidate_packet_path,
        )

        self.assertEqual(report["status"], "ANALYZED")
        self.assertFalse(report["release_gate"])
        self.assertNotIn("thresholds", report)
        self.assertNotIn("gates", report)
        self.assertEqual(report["coverage"]["context_count"], 2)
        self.assertEqual(report["coverage"]["independent_review_count"], 4)
        self.assertEqual(
            report["input"]["ratings_sha256"],
            hashlib.sha256(self.ratings_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            report["input"]["candidate_packet_sha256"],
            hashlib.sha256(
                self.candidate_packet_path.read_bytes()
            ).hexdigest(),
        )

        naturalness = report["scale_fields"]["naturalness"]
        self.assertEqual(naturalness["reviewer_pair_count"], 1)
        self.assertEqual(naturalness["reviewer_pair_context_count"], 2)
        self.assertEqual(naturalness["pairwise_comparison_count"], 6)
        self.assertEqual(naturalness["exact_agreement_count"], 6)
        self.assertEqual(naturalness["exact_agreement_rate"], 1.0)
        self.assertEqual(naturalness["mean_absolute_difference"], 0.0)
        self.assertEqual(naturalness["quadratic_weighted_kappa"], 1.0)
        self.assertEqual(
            naturalness["quadratic_weighted_kappa_status"],
            "defined",
        )

        faithfulness = report["boolean_fields"]["faithfulness_pass"]
        self.assertEqual(faithfulness["agreement_count"], 6)
        self.assertEqual(faithfulness["agreement_rate"], 1.0)
        self.assertEqual(faithfulness["cohen_kappa"], 1.0)

        preference = report["preference"]
        self.assertEqual(preference["pairwise_comparison_count"], 2)
        self.assertEqual(preference["agreement_count"], 2)
        self.assertEqual(preference["agreement_rate"], 1.0)
        self.assertEqual(preference["cohen_kappa"], 1.0)

    def test_reports_score_boolean_and_preference_disagreement(self) -> None:
        records = []
        for context_index, context_id in enumerate(("CTX-01", "CTX-02")):
            records.append(
                self._review(
                    context_id,
                    "REVIEWER-X",
                    preference_index=context_index,
                )
            )
            records.append(
                self._review(
                    context_id,
                    "REVIEWER-Y",
                    naturalness=(5, 4, 1),
                    faithfulness=(False, True, False),
                    preference_index=1 - context_index,
                )
            )
        self._write_ratings(records)

        report = analyze_inter_rater_agreement(
            ratings_path=self.ratings_path,
            candidate_packet_path=self.candidate_packet_path,
        )

        naturalness = report["scale_fields"]["naturalness"]
        self.assertEqual(naturalness["exact_agreement_count"], 2)
        self.assertEqual(naturalness["exact_agreement_rate"], 0.3333)
        self.assertEqual(naturalness["mean_absolute_difference"], 2.0)
        self.assertLess(naturalness["quadratic_weighted_kappa"], 1.0)
        self.assertEqual(
            naturalness["quadratic_weighted_kappa_status"],
            "defined",
        )

        faithfulness = report["boolean_fields"]["faithfulness_pass"]
        self.assertEqual(faithfulness["agreement_count"], 0)
        self.assertEqual(faithfulness["agreement_rate"], 0.0)
        self.assertEqual(faithfulness["cohen_kappa"], -0.8)

        preference = report["preference"]
        self.assertEqual(preference["agreement_count"], 0)
        self.assertEqual(preference["agreement_rate"], 0.0)
        self.assertEqual(preference["cohen_kappa"], -1.0)

    def test_supports_three_reviewers_per_context(self) -> None:
        self._write_ratings(
            self._perfect_records(
                ("REVIEWER-X", "REVIEWER-Y", "REVIEWER-Z")
            )
        )

        report = analyze_inter_rater_agreement(
            ratings_path=self.ratings_path,
            candidate_packet_path=self.candidate_packet_path,
        )

        self.assertEqual(report["coverage"]["reviewer_count"], 3)
        self.assertEqual(report["coverage"]["independent_review_count"], 6)
        clarity = report["scale_fields"]["clarity"]
        self.assertEqual(clarity["reviewer_pair_count"], 3)
        self.assertEqual(clarity["reviewer_pair_context_count"], 6)
        self.assertEqual(clarity["pairwise_comparison_count"], 18)
        self.assertEqual(clarity["exact_agreement_count"], 18)
        preference = report["preference"]
        self.assertEqual(preference["reviewer_pair_count"], 3)
        self.assertEqual(preference["reviewer_pair_context_count"], 6)
        self.assertEqual(preference["pairwise_comparison_count"], 6)

    def test_exact_text_tie_does_not_lower_preference_agreement(self) -> None:
        self._write_candidate_packet(
            exact_tie_contexts=frozenset({"CTX-01"})
        )
        records = self._perfect_records()
        for review in records:
            if review["context_id"] != "CTX-01":
                continue
            left = review["candidate_ratings"][0]
            right = review["candidate_ratings"][1]
            right.update(
                {
                    key: value
                    for key, value in left.items()
                    if key != "candidate_id"
                }
            )
            if review["reviewer_id"] == "REVIEWER-Y":
                review["baseline_humanistic_preference"] = (
                    "CTX-01-C2"
                )
        self._write_ratings(records)

        report = analyze_inter_rater_agreement(
            ratings_path=self.ratings_path,
            candidate_packet_path=self.candidate_packet_path,
        )

        preference = report["preference"]
        self.assertEqual(
            preference["excluded_exact_tie_context_count"],
            1,
        )
        self.assertEqual(
            preference["excluded_exact_tie_comparison_count"],
            1,
        )
        self.assertIn(
            "non-identifiable",
            preference["excluded_exact_tie_reason"],
        )
        self.assertEqual(preference["pairwise_comparison_count"], 1)
        self.assertEqual(preference["agreement_count"], 1)
        self.assertEqual(preference["agreement_rate"], 1.0)
        self.assertEqual(preference["cohen_kappa"], 1.0)
        self.assertEqual(
            report["scale_fields"]["naturalness"][
                "pairwise_comparison_count"
            ],
            6,
        )
        self.assertEqual(
            report["boolean_fields"]["faithfulness_pass"][
                "pairwise_comparison_count"
            ],
            6,
        )

    def test_rejects_rating_candidate_mapping_mismatch(self) -> None:
        records = self._perfect_records()
        records[0]["candidate_ratings"][2]["candidate_id"] = (
            "CTX-01-UNKNOWN"
        )
        self._write_ratings(records)

        with self.assertRaisesRegex(
            AgreementInputError,
            "must exactly match candidate packet",
        ):
            analyze_inter_rater_agreement(
                ratings_path=self.ratings_path,
                candidate_packet_path=self.candidate_packet_path,
            )

    def test_all_exact_ties_report_undefined_preference(self) -> None:
        self._write_candidate_packet(
            context_ids=("CTX-01",),
            exact_tie_contexts=frozenset({"CTX-01"}),
        )
        records = [
            self._review("CTX-01", "REVIEWER-X", preference_index=0),
            self._review("CTX-01", "REVIEWER-Y", preference_index=1),
        ]
        self._write_ratings(records)

        report = analyze_inter_rater_agreement(
            ratings_path=self.ratings_path,
            candidate_packet_path=self.candidate_packet_path,
        )

        preference = report["preference"]
        self.assertEqual(preference["pairwise_comparison_count"], 0)
        self.assertEqual(preference["agreement_count"], 0)
        self.assertIsNone(preference["agreement_rate"])
        self.assertIsNone(preference["cohen_kappa"])
        self.assertEqual(
            preference["cohen_kappa_status"],
            "undefined_no_eligible_non_tie_comparisons",
        )
        self.assertEqual(
            preference["excluded_exact_tie_context_count"],
            1,
        )
        self.assertEqual(
            preference["excluded_exact_tie_comparison_count"],
            1,
        )
        self.assertEqual(
            report["scale_fields"]["naturalness"][
                "pairwise_comparison_count"
            ],
            3,
        )

    def test_rejects_context_with_only_one_reviewer(self) -> None:
        self._write_candidate_packet(context_ids=("CTX-01",))
        self._write_ratings(
            [self._review("CTX-01", "REVIEWER-X")]
        )

        with self.assertRaisesRegex(
            AgreementInputError,
            "at least 2 independent reviewers",
        ):
            analyze_inter_rater_agreement(
                ratings_path=self.ratings_path,
                candidate_packet_path=self.candidate_packet_path,
            )

    def test_cli_writes_private_report_once(self) -> None:
        self._write_ratings(self._perfect_records())
        output_path = self.temp_dir / "reports" / "agreement.json"

        self.assertEqual(
            main(
                [
                    "--ratings",
                    str(self.ratings_path),
                    "--candidate-packet",
                    str(self.candidate_packet_path),
                    "--output",
                    str(output_path),
                ]
            ),
            0,
        )
        self.assertEqual(stat.S_IMODE(output_path.stat().st_mode), 0o600)
        self.assertEqual(
            main(
                [
                    "--ratings",
                    str(self.ratings_path),
                    "--candidate-packet",
                    str(self.candidate_packet_path),
                    "--output",
                    str(output_path),
                ]
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()

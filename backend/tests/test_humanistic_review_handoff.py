from __future__ import annotations

import copy
import hashlib
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.agents.humanistic_candidate_generation import (
    CandidateGenerationManifest,
    ExactModelTieRecord,
    GenerationProtocol,
    GenerationSourceHashes,
)
from app.agents.humanistic_review_handoff import (
    CandidateGenerationReceipt,
    EMPTY_FILE_SHA256,
    EXPECTED_COMPLETE_OUTPUTS,
    HandoffValidationError,
    ReceiptCounts,
    ReceiptModelIdentity,
    ReceiptVerificationScope,
    build_blank_rating_templates,
    build_evaluator_input_bundle,
    build_generation_receipt,
    build_unblinded_evaluator_ratings,
    load_generation_receipt,
    write_blank_rating_templates,
    write_evaluator_input_bundle,
    write_generation_receipt,
    write_unblinded_evaluator_ratings,
)
from scripts.analyze_humanistic_inter_rater_agreement_v1 import (
    analyze_inter_rater_agreement,
)
from scripts.evaluate_humanistic_interviewer_v1 import (
    _validate_arm_key,
    _validate_candidate_packet,
    _validate_ratings,
)


class HumanisticReviewHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_context = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_context.name)
        self.repo_root = self.root / "repo"
        self.repo_root.mkdir()
        self.private_root = self.root / "private"
        self.private_root.mkdir(mode=0o700)
        self.receipt_index = 0

    def tearDown(self) -> None:
        self.temp_context.cleanup()

    @staticmethod
    def _source_hashes() -> GenerationSourceHashes:
        value = "a" * 64
        return GenerationSourceHashes(
            context_manifest_sha256=value,
            generator_sha256=value,
            generator_cli_sha256=value,
            prompt_registry_sha256=value,
            interviewer_agent_sha256=value,
            output_contract_module_sha256=value,
            validator_sha256=value,
            context_adapter_sha256=value,
            model_gateway_sha256=value,
            config_sha256=value,
        )

    @staticmethod
    def _exact_tie() -> ExactModelTieRecord:
        return ExactModelTieRecord(
            run_id="run_" + "1" * 32,
            case_id="case_" + "2" * 32,
            context_id="HIV1-A01",
            split="train",
            paired_round=1,
            candidate_ids=[
                "cand_" + "3" * 32,
                "cand_" + "4" * 32,
            ],
            candidate_text_sha256="5" * 64,
            fallback_candidate_id="cand_" + "6" * 32,
            fallback_candidate_text_sha256="7" * 64,
        )

    def _write_complete_manifest(
        self,
        *,
        exact_tie_count: int = 0,
    ) -> tuple[Path, Path | None]:
        exact_ties_path: Path | None = None
        exact_tie_sha = EMPTY_FILE_SHA256
        if exact_tie_count:
            exact_ties_path = self.private_root / "exact_model_ties_v1.jsonl"
            exact_tie_bytes = (
                self._exact_tie().model_dump_json() + "\n"
            ).encode("utf-8")
            exact_ties_path.write_bytes(exact_tie_bytes)
            exact_tie_sha = hashlib.sha256(exact_tie_bytes).hexdigest()

        output_sha256 = {
            relative_path: "b" * 64
            for relative_path in EXPECTED_COMPLETE_OUTPUTS
        }
        output_sha256[
            "sealed/generation_failures_v1.jsonl"
        ] = EMPTY_FILE_SHA256
        output_sha256[
            "sealed/exact_model_ties_v1.jsonl"
        ] = exact_tie_sha
        manifest = CandidateGenerationManifest(
            status="complete",
            run_id="run_" + "1" * 32,
            context_count=2,
            attempted_context_count=2,
            candidate_count=6,
            case_key_count=2,
            arm_key_count=2,
            provenance_count=6,
            failure_count=0,
            exact_model_tie_count=exact_tie_count,
            source_hashes=self._source_hashes(),
            protocol=GenerationProtocol(
                provider="deepseek",
                model="deepseek-chat",
            ),
            output_sha256=output_sha256,
        )
        manifest_path = (
            self.private_root / "candidate_generation_manifest_v1.json"
        )
        manifest_path.write_text(
            manifest.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        return manifest_path, exact_ties_path

    @staticmethod
    def _blind_case(index: int) -> dict:
        return {
            "schema_version": "humanistic_blind_review_packet_v1",
            "case_id": f"case_{index:032x}",
            "review_context": {
                "visible_history": [
                    {
                        "turn_id": index,
                        "speaker": "user",
                        "content": f"仅用于单测的用户回答 {index}",
                    }
                ],
                "question_intent": "继续澄清依据",
                "allowed_facts": ["用户已说明一个判断"],
                "reflection_basis_turn_ids": [index],
                "expected_question_count": 1,
                "formal_answer": True,
            },
            "candidates": [
                {
                    "candidate_id": f"cand_{index * 10 + offset:032x}",
                    "candidate_text": (
                        f"不应进入评分模板的候选正文 {index}-{offset}？"
                    ),
                }
                for offset in range(1, 4)
            ],
        }

    def _write_blind_packet(self, records: list[dict]) -> Path:
        path = self.private_root / "blind_review_packet_v1.jsonl"
        path.write_text(
            "".join(
                json.dumps(record, ensure_ascii=False) + "\n"
                for record in records
            ),
            encoding="utf-8",
        )
        return path

    def _write_bound_receipt(
        self,
        *,
        context_count: int,
        packet_path: Path,
        case_key_path: Path | None = None,
        arm_key_path: Path | None = None,
        exact_ties_path: Path | None = None,
        exact_tie_count: int = 0,
        run_id: str = "run_" + "1" * 32,
    ) -> Path:
        output_sha256 = {
            relative_path: "b" * 64
            for relative_path in EXPECTED_COMPLETE_OUTPUTS
        }
        output_sha256[
            "reviewer/blind_review_packet_v1.jsonl"
        ] = hashlib.sha256(packet_path.read_bytes()).hexdigest()
        if case_key_path is not None:
            output_sha256[
                "sealed/case_key_v1.jsonl"
            ] = hashlib.sha256(case_key_path.read_bytes()).hexdigest()
        if arm_key_path is not None:
            output_sha256[
                "sealed/arm_key_v1.jsonl"
            ] = hashlib.sha256(arm_key_path.read_bytes()).hexdigest()
        output_sha256[
            "sealed/generation_failures_v1.jsonl"
        ] = EMPTY_FILE_SHA256
        output_sha256[
            "sealed/exact_model_ties_v1.jsonl"
        ] = (
            hashlib.sha256(exact_ties_path.read_bytes()).hexdigest()
            if exact_ties_path is not None
            else EMPTY_FILE_SHA256
        )
        receipt = CandidateGenerationReceipt(
            run_id=run_id,
            manifest_sha256="c" * 64,
            counts=ReceiptCounts(
                context_count=context_count,
                attempted_context_count=context_count,
                candidate_count=context_count * 3,
                case_key_count=context_count,
                arm_key_count=context_count,
                provenance_count=context_count * 3,
                failure_count=0,
                exact_model_tie_count=exact_tie_count,
            ),
            model_identity=ReceiptModelIdentity(
                provider="deepseek",
                model="deepseek-chat",
            ),
            source_sha256=self._source_hashes(),
            output_sha256=output_sha256,
            verification_scope=ReceiptVerificationScope(
                exact_ties=(
                    "file_hash_and_records_validated"
                    if exact_tie_count
                    else "manifest_declares_zero_and_empty_file_hash"
                )
            ),
        )
        self.receipt_index += 1
        receipt_path = (
            self.repo_root
            / "docs"
            / f"generation_receipt_{self.receipt_index}.json"
        )
        write_generation_receipt(receipt, receipt_path)
        return receipt_path

    @staticmethod
    def _write_jsonl(path: Path, records: list[dict]) -> Path:
        path.write_text(
            "".join(
                json.dumps(record, ensure_ascii=False) + "\n"
                for record in records
            ),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _complete_templates(
        templates: list[dict],
        *,
        reviewer_id: str,
    ) -> list[dict]:
        completed = copy.deepcopy(templates)
        for record in completed:
            record["review_status"] = "completed"
            record["reviewer_id"] = reviewer_id
            for rating in record["candidate_ratings"]:
                rating.update(
                    {
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
                )
            for preference in record["pairwise_preferences"]:
                preference["preferred_candidate_id"] = preference[
                    "candidate_ids"
                ][1]
        return completed

    def _write_unblinding_inputs(
        self,
    ) -> tuple[
        list[Path],
        Path,
        Path,
        Path,
        Path,
        list[dict],
        list[dict],
    ]:
        packet_path = self._write_blind_packet(
            [self._blind_case(1), self._blind_case(2)]
        )
        blind_cases = [self._blind_case(1), self._blind_case(2)]
        case_key: list[dict] = []
        arm_key: list[dict] = []
        for index, case in enumerate(blind_cases, start=1):
            candidate_ids = [
                candidate["candidate_id"]
                for candidate in case["candidates"]
            ]
            case_key.append(
                {
                    "case_id": case["case_id"],
                    "context_id": f"HIV1-A{index:02d}",
                    "split": "train",
                }
            )
            arm_key.append(
                {
                    "case_id": case["case_id"],
                    "assignments": [
                        {
                            "candidate_id": candidate_ids[0],
                            "arm": "baseline",
                        },
                        {
                            "candidate_id": candidate_ids[1],
                            "arm": "humanistic",
                        },
                        {
                            "candidate_id": candidate_ids[2],
                            "arm": "fallback",
                        },
                    ],
                }
            )
        case_key_path = self._write_jsonl(
            self.private_root / "case_key_v1.jsonl",
            case_key,
        )
        arm_key_path = self._write_jsonl(
            self.private_root / "arm_key_v1.jsonl",
            arm_key,
        )
        receipt_path = self._write_bound_receipt(
            context_count=2,
            packet_path=packet_path,
            case_key_path=case_key_path,
            arm_key_path=arm_key_path,
        )
        templates = build_blank_rating_templates(
            packet_path=packet_path,
            receipt_path=receipt_path,
            reviewer_id="REVIEWER-A",
            repo_root=self.repo_root,
        )
        reviewer_a = self._complete_templates(
            templates,
            reviewer_id="REVIEWER-A",
        )
        reviewer_b = self._complete_templates(
            templates,
            reviewer_id="REVIEWER-B",
        )
        ratings_paths = [
            self._write_jsonl(
                self.private_root / "reviewer-a.completed.jsonl",
                reviewer_a,
            ),
            self._write_jsonl(
                self.private_root / "reviewer-b.completed.jsonl",
                reviewer_b,
            ),
        ]
        return (
            ratings_paths,
            packet_path,
            receipt_path,
            case_key_path,
            arm_key_path,
            case_key,
            arm_key,
        )

    def test_generation_receipt_is_allowlisted_and_redacted(self) -> None:
        manifest_path, exact_ties_path = self._write_complete_manifest(
            exact_tie_count=1
        )

        receipt = build_generation_receipt(
            manifest_path=manifest_path,
            exact_ties_path=exact_ties_path,
            repo_root=self.repo_root,
            expected_context_count=2,
        )
        output_path = self.repo_root / "docs" / "generation_receipt_v1.json"
        write_generation_receipt(receipt, output_path)

        payload = json.loads(output_path.read_text(encoding="utf-8"))
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertEqual(
            payload["receipt_status"],
            "VERIFIED_COMPLETE_MANIFEST",
        )
        self.assertEqual(payload["generation_status"], "complete")
        self.assertEqual(payload["counts"]["candidate_count"], 6)
        self.assertEqual(payload["counts"]["exact_model_tie_count"], 1)
        self.assertEqual(
            payload["verification_scope"]["exact_ties"],
            "file_hash_and_records_validated",
        )
        self.assertFalse(payload["redaction"]["contains_candidate_text"])
        self.assertFalse(
            payload["redaction"]["contains_case_or_arm_key_records"]
        )
        self.assertFalse(payload["redaction"]["contains_provenance_records"])
        self.assertNotIn('"candidate_text"', serialized)
        self.assertNotIn('"case_id"', serialized)
        self.assertNotIn('"assignments"', serialized)
        self.assertNotIn('"candidate_id"', serialized)
        self.assertEqual(stat.S_IMODE(output_path.stat().st_mode), 0o644)

        with self.assertRaises(FileExistsError):
            write_generation_receipt(receipt, output_path)

    def test_generation_receipt_rejects_blocked_or_inconsistent_manifest(
        self,
    ) -> None:
        manifest_path, _ = self._write_complete_manifest()
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload.update(
            {
                "status": "blocked",
                "attempted_context_count": 1,
                "candidate_count": 0,
                "case_key_count": 1,
                "arm_key_count": 0,
                "provenance_count": 1,
                "blocked_context_ids": ["HIV1-A01"],
                "stop_reason": "unit_blocked",
                "stop_context_id": "HIV1-A01",
            }
        )
        manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            HandoffValidationError,
            "requires manifest status=complete",
        ):
            build_generation_receipt(
                manifest_path=manifest_path,
                exact_ties_path=None,
                repo_root=self.repo_root,
                expected_context_count=2,
            )

        manifest_path, _ = self._write_complete_manifest()
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["candidate_count"] = 5
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(
            HandoffValidationError,
            "exactly three candidates",
        ):
            build_generation_receipt(
                manifest_path=manifest_path,
                exact_ties_path=None,
                repo_root=self.repo_root,
                expected_context_count=2,
            )

    def test_generation_receipt_requires_and_hash_checks_exact_ties(
        self,
    ) -> None:
        manifest_path, exact_ties_path = self._write_complete_manifest(
            exact_tie_count=1
        )
        with self.assertRaisesRegex(
            HandoffValidationError,
            "evidence is required",
        ):
            build_generation_receipt(
                manifest_path=manifest_path,
                exact_ties_path=None,
                repo_root=self.repo_root,
                expected_context_count=2,
            )

        assert exact_ties_path is not None
        exact_ties_path.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(
            HandoffValidationError,
            "SHA-256 differs",
        ):
            build_generation_receipt(
                manifest_path=manifest_path,
                exact_ties_path=exact_ties_path,
                repo_root=self.repo_root,
                expected_context_count=2,
            )

    def test_private_receipt_inputs_must_be_outside_repository(self) -> None:
        manifest_path, _ = self._write_complete_manifest()
        inside_path = self.repo_root / manifest_path.name
        inside_path.write_bytes(manifest_path.read_bytes())

        with self.assertRaisesRegex(
            HandoffValidationError,
            "outside the Git repository",
        ):
            build_generation_receipt(
                manifest_path=inside_path,
                exact_ties_path=None,
                repo_root=self.repo_root,
                expected_context_count=2,
            )

    def test_rating_template_contains_only_opaque_ids_and_blank_judgments(
        self,
    ) -> None:
        packet_path = self._write_blind_packet(
            [self._blind_case(1), self._blind_case(2)]
        )
        receipt_path = self._write_bound_receipt(
            context_count=2,
            packet_path=packet_path,
        )

        templates = build_blank_rating_templates(
            packet_path=packet_path,
            receipt_path=receipt_path,
            reviewer_id="REVIEWER-A",
            repo_root=self.repo_root,
        )
        output_path = self.private_root / "ratings" / "reviewer-a.blank.jsonl"
        write_blank_rating_templates(
            templates,
            output_path,
            repo_root=self.repo_root,
        )

        serialized = output_path.read_text(encoding="utf-8")
        records = [
            json.loads(line)
            for line in serialized.splitlines()
        ]
        self.assertEqual(len(records), 2)
        self.assertTrue(
            all(record["reviewer_id"] == "REVIEWER-A" for record in records)
        )
        self.assertTrue(
            all(len(record["candidate_ratings"]) == 3 for record in records)
        )
        self.assertTrue(
            all(len(record["pairwise_preferences"]) == 3 for record in records)
        )
        self.assertTrue(
            all(
                rating["naturalness"] is None
                and rating["hard_error_codes"] is None
                for record in records
                for rating in record["candidate_ratings"]
            )
        )
        self.assertNotIn("candidate_text", serialized)
        self.assertNotIn("review_context", serialized)
        self.assertNotIn('"arm"', serialized)
        self.assertNotIn("baseline", serialized)
        self.assertNotIn("humanistic", serialized)
        self.assertNotIn("不应进入评分模板的候选正文", serialized)
        self.assertEqual(stat.S_IMODE(output_path.stat().st_mode), 0o600)

        with self.assertRaises(FileExistsError):
            write_blank_rating_templates(
                templates,
                output_path,
                repo_root=self.repo_root,
            )

    def test_rating_template_rejects_packet_tampered_after_receipt(
        self,
    ) -> None:
        packet_path = self._write_blind_packet(
            [self._blind_case(1), self._blind_case(2)]
        )
        receipt_path = self._write_bound_receipt(
            context_count=2,
            packet_path=packet_path,
        )
        records = [
            json.loads(line)
            for line in packet_path.read_text(encoding="utf-8").splitlines()
        ]
        sentinel = "TAMPERED-PRIVATE-CANDIDATE"
        records[0]["candidates"][0]["candidate_text"] = sentinel
        self._write_jsonl(packet_path, records)

        with self.assertRaisesRegex(
            HandoffValidationError,
            "packet SHA-256 differs",
        ) as raised:
            build_blank_rating_templates(
                packet_path=packet_path,
                receipt_path=receipt_path,
                reviewer_id="REVIEWER-A",
                repo_root=self.repo_root,
            )
        self.assertNotIn(sentinel, str(raised.exception))

    def test_strict_receipt_loader_rejects_tampered_count_contract(
        self,
    ) -> None:
        packet_path = self._write_blind_packet(
            [self._blind_case(1), self._blind_case(2)]
        )
        receipt_path = self._write_bound_receipt(
            context_count=2,
            packet_path=packet_path,
        )
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
        payload["counts"]["candidate_count"] = 5
        receipt_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            HandoffValidationError,
            "three candidates per context",
        ):
            load_generation_receipt(receipt_path)

    def test_rating_template_rejects_arm_leak_and_global_id_collision(
        self,
    ) -> None:
        leaked = self._blind_case(1)
        leaked["candidates"][0]["arm"] = "baseline"
        packet_path = self._write_blind_packet([leaked])
        receipt_path = self._write_bound_receipt(
            context_count=1,
            packet_path=packet_path,
        )
        with self.assertRaisesRegex(
            HandoffValidationError,
            "violates schema",
        ):
            build_blank_rating_templates(
                packet_path=packet_path,
                receipt_path=receipt_path,
                reviewer_id="REVIEWER-A",
                repo_root=self.repo_root,
            )

        first = self._blind_case(1)
        second = self._blind_case(2)
        second["candidates"][0]["candidate_id"] = first["candidates"][0][
            "candidate_id"
        ]
        packet_path.write_text(
            "".join(
                json.dumps(record, ensure_ascii=False) + "\n"
                for record in (first, second)
            ),
            encoding="utf-8",
        )
        receipt_path = self._write_bound_receipt(
            context_count=2,
            packet_path=packet_path,
        )
        with self.assertRaisesRegex(
            HandoffValidationError,
            "candidate IDs must be globally unique",
        ):
            build_blank_rating_templates(
                packet_path=packet_path,
                receipt_path=receipt_path,
                reviewer_id="REVIEWER-A",
                repo_root=self.repo_root,
            )

        with self.assertRaisesRegex(
            HandoffValidationError,
            "forbidden blind field",
        ):
            write_blank_rating_templates(
                [{"case_id": "case_" + "1" * 32, "arm": "baseline"}],
                self.private_root / "must-not-exist.jsonl",
                repo_root=self.repo_root,
            )

    def test_rating_packet_and_output_must_remain_outside_repository(
        self,
    ) -> None:
        packet_path = self.repo_root / "blind_review_packet_v1.jsonl"
        packet_path.write_text(
            json.dumps(self._blind_case(1), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        receipt_path = self._write_bound_receipt(
            context_count=1,
            packet_path=packet_path,
        )
        with self.assertRaisesRegex(
            HandoffValidationError,
            "outside the Git repository",
        ):
            build_blank_rating_templates(
                packet_path=packet_path,
                receipt_path=receipt_path,
                reviewer_id="REVIEWER-A",
                repo_root=self.repo_root,
            )

        external_packet = self._write_blind_packet([self._blind_case(1)])
        receipt_path = self._write_bound_receipt(
            context_count=1,
            packet_path=external_packet,
        )
        templates = build_blank_rating_templates(
            packet_path=external_packet,
            receipt_path=receipt_path,
            reviewer_id="REVIEWER-A",
            repo_root=self.repo_root,
        )
        with self.assertRaisesRegex(
            HandoffValidationError,
            "outside the Git repository",
        ):
            write_blank_rating_templates(
                templates,
                self.repo_root / "ratings.jsonl",
                repo_root=self.repo_root,
            )

    def test_custodian_unblinding_outputs_frozen_evaluator_contract_without_arm(
        self,
    ) -> None:
        (
            ratings_paths,
            packet_path,
            receipt_path,
            case_key_path,
            arm_key_path,
            _,
            arm_key,
        ) = self._write_unblinding_inputs()

        records = build_unblinded_evaluator_ratings(
            ratings_paths=ratings_paths,
            receipt_path=receipt_path,
            case_key_path=case_key_path,
            arm_key_path=arm_key_path,
            exact_ties_path=None,
            repo_root=self.repo_root,
        )
        optional_empty_ties = self.private_root / "optional-empty-ties.jsonl"
        optional_empty_ties.write_bytes(b"")
        records_with_checked_empty_ties = build_unblinded_evaluator_ratings(
            ratings_paths=ratings_paths,
            receipt_path=receipt_path,
            case_key_path=case_key_path,
            arm_key_path=arm_key_path,
            exact_ties_path=optional_empty_ties,
            repo_root=self.repo_root,
        )
        self.assertEqual(records_with_checked_empty_ties, records)
        optional_empty_ties.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(
            HandoffValidationError,
            "exact-model-tie SHA-256 differs",
        ):
            build_unblinded_evaluator_ratings(
                ratings_paths=ratings_paths,
                receipt_path=receipt_path,
                case_key_path=case_key_path,
                arm_key_path=arm_key_path,
                exact_ties_path=optional_empty_ties,
                repo_root=self.repo_root,
            )
        output_path = self.private_root / "evaluator-ratings.jsonl"
        write_unblinded_evaluator_ratings(
            records,
            output_path,
            repo_root=self.repo_root,
        )

        serialized = output_path.read_text(encoding="utf-8")
        payloads = [
            json.loads(line) for line in serialized.splitlines()
        ]
        self.assertEqual(len(payloads), 4)
        self.assertEqual(
            {record["context_id"] for record in payloads},
            {"HIV1-A01", "HIV1-A02"},
        )
        expected_humanistic_ids = {
            assignment["candidate_id"]
            for record in arm_key
            for assignment in record["assignments"]
            if assignment["arm"] == "humanistic"
        }
        self.assertEqual(
            {
                record["baseline_humanistic_preference"]
                for record in payloads
            },
            expected_humanistic_ids,
        )
        self.assertNotIn('"arm"', serialized)
        self.assertNotIn('"case_id"', serialized)
        self.assertNotIn("pairwise_preferences", serialized)
        self.assertNotIn("review_status", serialized)
        self.assertEqual(stat.S_IMODE(output_path.stat().st_mode), 0o600)
        evaluator_bundle = build_evaluator_input_bundle(
            packet_path=packet_path,
            ratings_paths=ratings_paths,
            receipt_path=receipt_path,
            case_key_path=case_key_path,
            arm_key_path=arm_key_path,
            exact_ties_path=None,
            repo_root=self.repo_root,
        )
        candidate_packet_path = self._write_jsonl(
            self.private_root / "evaluator-candidates.jsonl",
            evaluator_bundle.candidate_packet,
        )
        agreement = analyze_inter_rater_agreement(
            ratings_path=output_path,
            candidate_packet_path=candidate_packet_path,
        )
        self.assertEqual(agreement["status"], "ANALYZED")
        self.assertEqual(agreement["coverage"]["context_count"], 2)
        self.assertEqual(
            agreement["coverage"]["independent_review_count"],
            4,
        )
        with self.assertRaises(FileExistsError):
            write_unblinded_evaluator_ratings(
                records,
                output_path,
                repo_root=self.repo_root,
            )

    def test_custodian_unblinding_rejects_incomplete_or_duplicate_reviews(
        self,
    ) -> None:
        (
            ratings_paths,
            _,
            receipt_path,
            case_key_path,
            arm_key_path,
            _,
            _,
        ) = self._write_unblinding_inputs()
        reviewer_a = [
            json.loads(line)
            for line in ratings_paths[0].read_text(encoding="utf-8").splitlines()
        ]
        self._write_jsonl(ratings_paths[0], reviewer_a[:1])
        reviewer_b = [
            json.loads(line)
            for line in ratings_paths[1].read_text(encoding="utf-8").splitlines()
        ]
        self._write_jsonl(ratings_paths[1], reviewer_b[:1])
        with self.assertRaisesRegex(
            HandoffValidationError,
            "do not cover all sealed cases",
        ):
            build_unblinded_evaluator_ratings(
                ratings_paths=ratings_paths,
                receipt_path=receipt_path,
                case_key_path=case_key_path,
                arm_key_path=arm_key_path,
                exact_ties_path=None,
                repo_root=self.repo_root,
            )

        self._write_jsonl(ratings_paths[0], reviewer_a)
        duplicate_path = self._write_jsonl(
            self.private_root / "reviewer-a-copy.completed.jsonl",
            reviewer_a,
        )
        with self.assertRaisesRegex(
            HandoffValidationError,
            "duplicate frozen review",
        ):
            build_unblinded_evaluator_ratings(
                ratings_paths=[ratings_paths[0], duplicate_path, ratings_paths[1]],
                receipt_path=receipt_path,
                case_key_path=case_key_path,
                arm_key_path=arm_key_path,
                exact_ties_path=None,
                repo_root=self.repo_root,
            )

    def test_custodian_unblinding_rejects_key_mismatch_and_bad_exact_tie(
        self,
    ) -> None:
        (
            ratings_paths,
            packet_path,
            receipt_path,
            case_key_path,
            arm_key_path,
            case_key,
            arm_key,
        ) = self._write_unblinding_inputs()
        broken_arm_key = copy.deepcopy(arm_key)
        broken_arm_key[0]["assignments"][0][
            "candidate_id"
        ] = "cand_" + "f" * 32
        self._write_jsonl(arm_key_path, broken_arm_key)
        with self.assertRaisesRegex(
            HandoffValidationError,
            "SHA-256 differs",
        ):
            build_unblinded_evaluator_ratings(
                ratings_paths=ratings_paths,
                receipt_path=receipt_path,
                case_key_path=case_key_path,
                arm_key_path=arm_key_path,
                exact_ties_path=None,
                repo_root=self.repo_root,
            )

        self._write_jsonl(arm_key_path, arm_key)
        first_assignments = {
            assignment["arm"]: assignment["candidate_id"]
            for assignment in arm_key[0]["assignments"]
        }
        tie = ExactModelTieRecord(
            run_id="run_" + "9" * 32,
            case_id=case_key[0]["case_id"],
            context_id=case_key[0]["context_id"],
            split=case_key[0]["split"],
            paired_round=1,
            candidate_ids=[
                first_assignments["baseline"],
                first_assignments["humanistic"],
            ],
            candidate_text_sha256="8" * 64,
            fallback_candidate_id=first_assignments["fallback"],
            fallback_candidate_text_sha256="7" * 64,
        )
        exact_ties_path = self._write_jsonl(
            self.private_root / "exact_ties_for_unblind.jsonl",
            [tie.model_dump(mode="json")],
        )
        exact_receipt_path = self._write_bound_receipt(
            context_count=2,
            packet_path=packet_path,
            case_key_path=case_key_path,
            arm_key_path=arm_key_path,
            exact_ties_path=exact_ties_path,
            exact_tie_count=1,
            run_id="run_" + "9" * 32,
        )
        with self.assertRaisesRegex(
            HandoffValidationError,
            "requires a sealed exact-model-tie file",
        ):
            build_unblinded_evaluator_ratings(
                ratings_paths=ratings_paths,
                receipt_path=exact_receipt_path,
                case_key_path=case_key_path,
                arm_key_path=arm_key_path,
                exact_ties_path=None,
                repo_root=self.repo_root,
            )
        reviewer_a = [
            json.loads(line)
            for line in ratings_paths[0].read_text(encoding="utf-8").splitlines()
        ]
        reviewer_a[0]["candidate_ratings"][1]["warmth"] = 3
        self._write_jsonl(ratings_paths[0], reviewer_a)
        with self.assertRaisesRegex(
            HandoffValidationError,
            "exact-tie candidates require identical",
        ):
            build_unblinded_evaluator_ratings(
                ratings_paths=ratings_paths,
                receipt_path=exact_receipt_path,
                case_key_path=case_key_path,
                arm_key_path=arm_key_path,
                exact_ties_path=exact_ties_path,
                repo_root=self.repo_root,
            )

    def test_prepare_evaluator_bundle_matches_frozen_validator_contracts(
        self,
    ) -> None:
        (
            ratings_paths,
            packet_path,
            receipt_path,
            case_key_path,
            arm_key_path,
            _,
            _,
        ) = self._write_unblinding_inputs()
        bundle = build_evaluator_input_bundle(
            packet_path=packet_path,
            ratings_paths=ratings_paths,
            receipt_path=receipt_path,
            case_key_path=case_key_path,
            arm_key_path=arm_key_path,
            exact_ties_path=None,
            repo_root=self.repo_root,
        )
        output_dir = self.private_root / "evaluator-inputs"
        manifest = write_evaluator_input_bundle(
            bundle,
            output_dir,
            repo_root=self.repo_root,
        )

        candidate_records = [
            json.loads(line)
            for line in (
                output_dir / "candidate_packet_v1.jsonl"
            ).read_text(encoding="utf-8").splitlines()
        ]
        rating_records = [
            json.loads(line)
            for line in (
                output_dir / "ratings_v1.jsonl"
            ).read_text(encoding="utf-8").splitlines()
        ]
        arm_records = [
            json.loads(line)
            for line in (
                output_dir / "arm_key_v1.jsonl"
            ).read_text(encoding="utf-8").splitlines()
        ]
        context_ids = {"HIV1-A01", "HIV1-A02"}
        candidates_by_context = _validate_candidate_packet(
            candidate_records,
            context_ids,
        )
        assignments = _validate_arm_key(
            arm_records,
            candidates_by_context,
        )
        flattened, preferences = _validate_ratings(
            rating_records,
            candidates_by_context,
            assignments,
        )
        self.assertEqual(len(flattened), 12)
        self.assertEqual(len(preferences), 4)

        manifest_path = output_dir / "evaluator_input_manifest_v1.json"
        manifest_payload = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )
        manifest_serialized = json.dumps(
            manifest_payload,
            ensure_ascii=False,
        )
        self.assertEqual(manifest_payload, manifest)
        self.assertNotIn('"candidate_text":', manifest_serialized)
        self.assertNotIn('"arm":', manifest_serialized)
        self.assertNotIn('"assignments"', manifest_serialized)
        self.assertNotIn("不应进入评分模板的候选正文", manifest_serialized)
        self.assertEqual(stat.S_IMODE(output_dir.stat().st_mode), 0o700)
        for path in output_dir.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        for filename, expected_sha256 in manifest[
            "output_sha256"
        ].items():
            self.assertEqual(
                hashlib.sha256(
                    (output_dir / filename).read_bytes()
                ).hexdigest(),
                expected_sha256,
            )
        with self.assertRaises(FileExistsError):
            write_evaluator_input_bundle(
                bundle,
                output_dir,
                repo_root=self.repo_root,
            )

        with self.assertRaisesRegex(
            HandoffValidationError,
            "outside the Git repository",
        ):
            write_unblinded_evaluator_ratings(
                [],
                self.repo_root / "unblinded-ratings.jsonl",
                repo_root=self.repo_root,
            )

    def test_cli_help_exposes_both_safe_handoff_commands(self) -> None:
        backend_root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/prepare_humanistic_review_handoff_v1.py",
                "--help",
            ],
            cwd=backend_root,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("generation-receipt", completed.stdout)
        self.assertIn("ratings-template", completed.stdout)
        self.assertIn("unblind-ratings", completed.stdout)
        self.assertIn("prepare-evaluator-inputs", completed.stdout)
        for command in (
            "ratings-template",
            "unblind-ratings",
            "prepare-evaluator-inputs",
        ):
            command_help = subprocess.run(
                [
                    sys.executable,
                    "scripts/prepare_humanistic_review_handoff_v1.py",
                    command,
                    "--help",
                ],
                cwd=backend_root,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                command_help.returncode,
                0,
                command_help.stderr,
            )
            self.assertIn("--receipt", command_help.stdout)

    def test_cli_schema_error_does_not_echo_private_candidate_text(
        self,
    ) -> None:
        leaked = self._blind_case(1)
        leaked["candidates"][0]["arm"] = "baseline"
        sentinel = "PRIVATE-CANDIDATE-TEXT-MUST-NOT-LEAK"
        leaked["candidates"][0]["candidate_text"] = sentinel
        packet_path = self._write_blind_packet([leaked])
        receipt_path = self._write_bound_receipt(
            context_count=1,
            packet_path=packet_path,
        )
        output_path = self.private_root / "must-not-exist.jsonl"
        backend_root = Path(__file__).resolve().parents[1]

        completed = subprocess.run(
            [
                sys.executable,
                "scripts/prepare_humanistic_review_handoff_v1.py",
                "ratings-template",
                "--packet",
                str(packet_path),
                "--receipt",
                str(receipt_path),
                "--reviewer-id",
                "REVIEWER-A",
                "--output",
                str(output_path),
            ],
            cwd=backend_root,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("BLOCKED", completed.stderr)
        self.assertNotIn(sentinel, completed.stderr)
        self.assertNotIn("baseline", completed.stderr)
        self.assertFalse(output_path.exists())


if __name__ == "__main__":
    unittest.main()

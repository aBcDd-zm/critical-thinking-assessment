from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import yaml
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.agents.humanistic_evaluation_context import load_context_manifest
from app.models import Base
from app.models.prompt import PromptTemplate
from scripts.seed_db import seed_prompts
from scripts.evaluate_humanistic_interviewer_v1_1 import (
    APPROVAL_SCHEMA,
    BLIND_REVIEW_VERSION,
    DEFAULT_CONFIG_PATH,
    DEFAULT_CONTEXTS_PATH,
    EVIDENCE_NAMESPACE,
    GENERATION_VERSION,
    MEASUREMENT_POLICY_VERSION,
    PROMPT_VERSION,
    RECEIPT_SCHEMA,
    SOURCE_BUNDLE_VERSION,
    STYLE_VERSION,
    evaluate_release_gate,
    main,
    runtime_source_bundle_sha256,
    runtime_source_hashes,
    sha256_file,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent


class HumanisticInterviewerV11AssetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_context = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self.temp_context.name)

    def tearDown(self) -> None:
        self.temp_context.cleanup()

    @staticmethod
    def _write_jsonl(path: Path, records: list[dict]) -> None:
        path.write_text(
            "".join(
                json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
                for record in records
            ),
            encoding="utf-8",
        )

    def _build_complete_v11_evidence(self) -> dict[str, Path]:
        contexts = load_context_manifest(
            DEFAULT_CONTEXTS_PATH,
            repo_root=REPO_ROOT,
            require_frozen=True,
        )
        paths = {
            key: self.temp_dir / f"{key}.jsonl"
            for key in (
                "candidate_packet",
                "ratings",
                "arm_key",
                "runtime_records",
                "uat_records",
            )
        }
        paths["measurement_approval"] = self.temp_dir / "approval.json"
        paths["receipt"] = self.temp_dir / "receipt.json"

        candidate_records: list[dict] = []
        arm_records: list[dict] = []
        rating_records: list[dict] = []
        runtime_records: list[dict] = []
        for context_index, context in enumerate(contexts, start=1):
            context_id = context.context_id
            candidate_ids = [
                f"V11-{context_index:02d}-C{candidate_index}"
                for candidate_index in range(1, 4)
            ]
            candidate_records.append(
                {
                    "evidence_namespace": EVIDENCE_NAMESPACE,
                    "context_id": context_id,
                    "candidates": [
                        {
                            "candidate_id": candidate_id,
                            "candidate_text": (
                                f"v1.1独立门禁算法单测候选"
                                f"{context_index}-{candidate_index}？"
                            ),
                        }
                        for candidate_index, candidate_id in enumerate(
                            candidate_ids, start=1
                        )
                    ],
                }
            )
            arm_records.append(
                {
                    "evidence_namespace": EVIDENCE_NAMESPACE,
                    "context_id": context_id,
                    "assignments": [
                        {"candidate_id": candidate_ids[0], "arm": "baseline"},
                        {"candidate_id": candidate_ids[1], "arm": "humanistic"},
                        {"candidate_id": candidate_ids[2], "arm": "fallback"},
                    ],
                }
            )
            for reviewer_id in ("UNIT-REVIEWER-A", "UNIT-REVIEWER-B"):
                rating_records.append(
                    {
                        "evidence_namespace": EVIDENCE_NAMESPACE,
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
            runtime_records.append(
                {
                    "evidence_namespace": EVIDENCE_NAMESPACE,
                    "style_version": STYLE_VERSION,
                    "prompt_version": PROMPT_VERSION,
                    "runtime_source_bundle_sha256": (
                        runtime_source_bundle_sha256()
                    ),
                    "record_id": f"V11-RUN-{context_index:02d}",
                    "context_id": context_id,
                    "total_latency_ms": 5000,
                    "renderer_fallback": False,
                    "validation_codes": [],
                    "hard_error_codes": [],
                }
            )
        uat_records = [
            {
                "evidence_namespace": EVIDENCE_NAMESPACE,
                "style_version": STYLE_VERSION,
                "prompt_version": PROMPT_VERSION,
                "runtime_source_bundle_sha256": (
                    runtime_source_bundle_sha256()
                ),
                "uat_run_id": f"V11-UAT-{index:02d}",
                "tester_id": f"UNIT-TESTER-{(index % 2) + 1}",
                "evidence_ref": f"unit-test://v1.1/uat/{index:02d}",
                "completed": True,
                "outcome": "pass",
                "open_critical_issue": False,
            }
            for index in range(1, 11)
        ]
        for key, records in (
            ("candidate_packet", candidate_records),
            ("ratings", rating_records),
            ("arm_key", arm_records),
            ("runtime_records", runtime_records),
            ("uat_records", uat_records),
        ):
            self._write_jsonl(paths[key], records)

        approval = {
            "schema_version": APPROVAL_SCHEMA,
            "evidence_namespace": EVIDENCE_NAMESPACE,
            "measurement_policy_version": MEASUREMENT_POLICY_VERSION,
            "approver_role": "member_a",
            "approved": True,
            "evidence_ref": "unit-test://member-a-approval",
        }
        paths["measurement_approval"].write_text(
            json.dumps(approval, ensure_ascii=False),
            encoding="utf-8",
        )
        receipt = {
            "schema_version": RECEIPT_SCHEMA,
            "receipt_status": "VERIFIED_COMPLETE_V1_1_EVIDENCE",
            "evidence_namespace": EVIDENCE_NAMESPACE,
            "style_version": STYLE_VERSION,
            "prompt_version": PROMPT_VERSION,
            "candidate_generation_version": GENERATION_VERSION,
            "blind_review_version": BLIND_REVIEW_VERSION,
            "measurement_policy_version": MEASUREMENT_POLICY_VERSION,
            "config_sha256": sha256_file(DEFAULT_CONFIG_PATH),
            "context_manifest_sha256": sha256_file(DEFAULT_CONTEXTS_PATH),
            "runtime_source_bundle_version": SOURCE_BUNDLE_VERSION,
            "runtime_source_bundle_sha256": runtime_source_bundle_sha256(),
            "runtime_sources": runtime_source_hashes(),
            "files": {
                key: {"sha256": sha256_file(paths[key])}
                for key in (
                    "candidate_packet",
                    "ratings",
                    "arm_key",
                    "runtime_records",
                    "uat_records",
                    "measurement_approval",
                )
            },
        }
        paths["receipt"].write_text(
            json.dumps(receipt, ensure_ascii=False),
            encoding="utf-8",
        )
        return paths

    def test_prompt_seed_is_versioned_without_replacing_v1(self) -> None:
        seed_path = BACKEND_ROOT / "seeds" / "runtime_prompts.yaml"
        payload = yaml.safe_load(seed_path.read_text(encoding="utf-8"))
        templates = {
            (item["template_code"], item["version"]): item
            for item in payload["templates"]
            if item["agent_name"] == "interviewer"
        }

        self.assertIn(
            (
                "humanistic_interviewer_compact_v1",
                "humanistic_interviewer_compact_v1",
            ),
            templates,
        )
        self.assertIn((PROMPT_VERSION, PROMPT_VERSION), templates)
        self.assertEqual(
            templates[(PROMPT_VERSION, PROMPT_VERSION)]["status"],
            "disabled",
        )
        current_prompt_version = "humanistic_compact_v1_2"
        self.assertIn(
            (current_prompt_version, current_prompt_version),
            templates,
        )
        self.assertEqual(
            templates[(current_prompt_version, current_prompt_version)]["status"],
            "active",
        )
        self.assertTrue(
            {
                "validated_plan",
                "draft",
                "candidate_intent_key",
                "question_candidates",
                "selected_candidate_id",
                "selected_question",
                "required_fact",
                "reflection_source_quotes",
            }.issubset(
                templates[(PROMPT_VERSION, PROMPT_VERSION)][
                    "input_schema_json"
                ]["required"]
            )
        )
        self.assertTrue(
            {
                "event_intro_selector_version",
                "previous_event_intro_frame",
                "selected_event_intro_frame",
            }.issubset(
                templates[(current_prompt_version, current_prompt_version)][
                    "input_schema_json"
                ]["required"]
            )
        )
        self.assertEqual(PromptTemplate.__table__.c.version.type.length, 64)

    def test_seeded_v11_prompt_resolves_by_exact_code_and_version(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            seed_prompts(session, BACKEND_ROOT / "seeds")
            current_prompt_version = "humanistic_compact_v1_2"
            v11 = session.execute(
                select(PromptTemplate).where(
                    PromptTemplate.agent_name == "interviewer",
                    PromptTemplate.template_code == current_prompt_version,
                    PromptTemplate.version == current_prompt_version,
                    PromptTemplate.status == "active",
                )
            ).scalar_one()
            legacy = session.execute(
                select(PromptTemplate).where(
                    PromptTemplate.agent_name == "interviewer",
                    PromptTemplate.template_code
                    == "humanistic_interviewer_compact_v1",
                    PromptTemplate.version
                    == "humanistic_interviewer_compact_v1",
                    PromptTemplate.status == "disabled",
                )
            ).scalar_one()
            historical_v11 = session.execute(
                select(PromptTemplate).where(
                    PromptTemplate.agent_name == "interviewer",
                    PromptTemplate.template_code == PROMPT_VERSION,
                    PromptTemplate.version == PROMPT_VERSION,
                    PromptTemplate.status == "disabled",
                )
            ).scalar_one()

        self.assertEqual(v11.name, "V1.2 罗杰斯式人本测评话术模板")
        self.assertEqual(
            legacy.name,
            "V1 轻量人本式测评话术模板",
        )
        self.assertEqual(
            historical_v11.name,
            "V1.1 罗杰斯式人本测评话术模板",
        )

    def test_default_configuration_remains_disabled_baseline(self) -> None:
        for path in (
            BACKEND_ROOT / ".env.example",
            REPO_ROOT / ".env.production.example",
        ):
            content = path.read_text(encoding="utf-8")
            self.assertIn("INTERVIEWER_STYLE_ENABLED=false", content)
            self.assertIn("INTERVIEWER_STYLE_DEFAULT=baseline_v1", content)

        config = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertFalse(config["evidence_contract"]["legacy_v1_evidence_accepted"])
        self.assertEqual(
            config["evidence_contract"]["runtime_source_binding"][
                "bundle_version"
            ],
            SOURCE_BUNDLE_VERSION,
        )
        self.assertIn(
            "candidate_intent_registry",
            config["evidence_contract"]["runtime_source_binding"][
                "required_sources"
            ],
        )
        self.assertEqual(
            runtime_source_hashes()["candidate_intent_registry"]["path"],
            "backend/app/agents/humanistic_v11_intent_registry.py",
        )
        self.assertFalse(config["release_gate"]["default_enabled"])
        self.assertEqual(config["release_gate"]["default_style"], "baseline_v1")

    def test_missing_v11_evidence_is_blocked_and_cli_returns_two(self) -> None:
        report = evaluate_release_gate()

        self.assertEqual(report["status"], "BLOCKED")
        self.assertIn(
            "receipt: v1.1 evidence receipt not supplied",
            report["blockers"],
        )
        with contextlib.redirect_stdout(io.StringIO()):
            exit_code = main([])
        self.assertEqual(exit_code, 2)

    def test_legacy_records_without_v11_namespace_are_blocked(self) -> None:
        paths = self._build_complete_v11_evidence()
        records = [
            json.loads(line)
            for line in paths["candidate_packet"]
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        records[0].pop("evidence_namespace")
        self._write_jsonl(paths["candidate_packet"], records)

        report = evaluate_release_gate(
            receipt_path=paths["receipt"],
            candidate_packet_path=paths["candidate_packet"],
            ratings_path=paths["ratings"],
            arm_key_path=paths["arm_key"],
            runtime_records_path=paths["runtime_records"],
            uat_records_path=paths["uat_records"],
            measurement_approval_path=paths["measurement_approval"],
        )

        self.assertEqual(report["status"], "BLOCKED")
        self.assertTrue(
            any("evidence_namespace" in blocker for blocker in report["blockers"])
        )

    def test_runtime_source_bundle_mismatch_is_blocked(self) -> None:
        paths = self._build_complete_v11_evidence()
        receipt = json.loads(paths["receipt"].read_text(encoding="utf-8"))
        receipt["runtime_sources"]["runtime_renderer"]["sha256"] = "0" * 64
        paths["receipt"].write_text(
            json.dumps(receipt, ensure_ascii=False),
            encoding="utf-8",
        )

        report = evaluate_release_gate(
            receipt_path=paths["receipt"],
            candidate_packet_path=paths["candidate_packet"],
            ratings_path=paths["ratings"],
            arm_key_path=paths["arm_key"],
            runtime_records_path=paths["runtime_records"],
            uat_records_path=paths["uat_records"],
            measurement_approval_path=paths["measurement_approval"],
        )

        self.assertEqual(report["status"], "BLOCKED")
        self.assertTrue(
            any(
                "runtime_sources.runtime_renderer.sha256" in blocker
                for blocker in report["blockers"]
            )
        )

    def test_candidate_intent_registry_is_bound_to_runtime_receipt(self) -> None:
        paths = self._build_complete_v11_evidence()
        receipt = json.loads(paths["receipt"].read_text(encoding="utf-8"))
        receipt["runtime_sources"]["candidate_intent_registry"]["sha256"] = (
            "0" * 64
        )
        paths["receipt"].write_text(
            json.dumps(receipt, ensure_ascii=False),
            encoding="utf-8",
        )

        report = evaluate_release_gate(
            receipt_path=paths["receipt"],
            candidate_packet_path=paths["candidate_packet"],
            ratings_path=paths["ratings"],
            arm_key_path=paths["arm_key"],
            runtime_records_path=paths["runtime_records"],
            uat_records_path=paths["uat_records"],
            measurement_approval_path=paths["measurement_approval"],
        )

        self.assertEqual(report["status"], "BLOCKED")
        self.assertTrue(
            any(
                "runtime_sources.candidate_intent_registry.sha256" in blocker
                for blocker in report["blockers"]
            )
        )

    def test_complete_namespaced_and_receipted_evidence_can_reach_v1_gates(
        self,
    ) -> None:
        paths = self._build_complete_v11_evidence()

        report = evaluate_release_gate(
            receipt_path=paths["receipt"],
            candidate_packet_path=paths["candidate_packet"],
            ratings_path=paths["ratings"],
            arm_key_path=paths["arm_key"],
            runtime_records_path=paths["runtime_records"],
            uat_records_path=paths["uat_records"],
            measurement_approval_path=paths["measurement_approval"],
        )

        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["evaluator"], "humanistic_interviewer_v1_1")
        self.assertEqual(report["evidence_namespace"], EVIDENCE_NAMESPACE)
        self.assertEqual(report["metrics"]["context_count"], 48)
        self.assertEqual(report["metrics"]["candidate_count"], 144)


if __name__ == "__main__":
    unittest.main()

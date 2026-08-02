from __future__ import annotations

import copy
import json
import re
import unittest
from collections import Counter
from pathlib import Path

import yaml

from app.agents.humanistic_evaluation_context import (
    CONTEXT_SCHEMA_VERSION,
    build_evaluation_blueprint,
    build_renderer_input_payload,
    build_runtime_context,
    load_context_manifest,
)
from app.agents.interviewer_agent import HUMANISTIC_INTERVIEWER_STYLE


REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs" / "humanistic_interviewer"
FIXTURES_DIR = (
    REPO_ROOT / "backend" / "tests" / "fixtures" / "humanistic_interviewer"
)
POLICY_PATH = DOCS_DIR / "humanistic_style_policy_v1.yaml"
INTERVIEWER_AGENT_PATH = (
    REPO_ROOT / "backend" / "app" / "agents" / "interviewer_agent.py"
)
README_PATH = DOCS_DIR / "README.md"
SOURCE_AUDIT_PATH = DOCS_DIR / "source_audit_v1.md"
SOURCE_LEDGER_PATH = DOCS_DIR / "source_ledger_v1.jsonl"
SOURCE_NOTES_PATH = DOCS_DIR / "humanistic_source_notes_v1.md"
DEVELOPMENT_CONTEXTS_PATH = FIXTURES_DIR / "pilot_contexts_development_v1.jsonl"
LOCKED_CONTEXTS_PATH = FIXTURES_DIR / "pilot_contexts_locked_v1.jsonl"
CONTEXT_MANIFEST_PATH = FIXTURES_DIR / "pilot_context_manifest_v1.json"
EXAMPLES_PATH = FIXTURES_DIR / "review_examples_v1.jsonl"

ZERO_TOLERANCE_LABELS = {
    "hidden_meaning",
    "attachment",
    "role_substitution",
    "self_disclosure",
    "authority_advice",
}
HARD_ERROR_CODES = {
    "unsupported_hidden_meaning",
    "relational_attachment",
    "role_substitution",
    "fabricated_self_disclosure",
    "prescriptive_authority",
    "clinical_role_claim",
}
CANONICAL_DIMENSION_KEYS = {
    "problem_definition",
    "evidence_evaluation",
    "reasoning_argumentation",
    "multiple_perspectives",
    "integrative_decision",
    "dynamic_adjustment",
}
EXPECTED_CONTEXT_DIMENSION_COUNTS = Counter(
    {
        "problem_definition": 5,
        "evidence_evaluation": 13,
        "reasoning_argumentation": 3,
        "multiple_perspectives": 3,
        "integrative_decision": 14,
        "dynamic_adjustment": 10,
    }
)
SOURCE_CONTAMINATION_BLOCKS = {
    "联系远远超出普通治疗关系",
    "更好地理解我自己，以及我如何与这个世界互动",
    "继续成长和深化我的治疗方法",
}
OTHER_UNSUPPORTED_SOURCE_PHRASES = {
    "我们有三刻钟时间",
}


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _serialized(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class HumanisticInterviewerCorpusAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.development_contexts = _load_jsonl(DEVELOPMENT_CONTEXTS_PATH)
        cls.locked_contexts = _load_jsonl(LOCKED_CONTEXTS_PATH)
        cls.contexts = cls.development_contexts + cls.locked_contexts
        cls.context_records = load_context_manifest(
            CONTEXT_MANIFEST_PATH,
            repo_root=REPO_ROOT,
        )
        cls.context_manifest = json.loads(
            CONTEXT_MANIFEST_PATH.read_text(encoding="utf-8")
        )
        cls.examples = _load_jsonl(EXAMPLES_PATH)
        cls.policy = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
        cls.readme = README_PATH.read_text(encoding="utf-8")
        cls.source_audit = SOURCE_AUDIT_PATH.read_text(encoding="utf-8")
        cls.source_ledger = _load_jsonl(SOURCE_LEDGER_PATH)
        cls.source_notes = SOURCE_NOTES_PATH.read_text(encoding="utf-8")
        cls.interviewer_agent_source = INTERVIEWER_AGENT_PATH.read_text(
            encoding="utf-8"
        )

    def test_frozen_source_principles_are_fully_mapped_into_policy(self) -> None:
        source_ids = set(
            re.findall(r"^### (HSP-\d{2})：", self.source_notes, re.MULTILINE)
        )
        self.assertIn("状态：`frozen_v1`", self.source_notes)
        self.assertEqual(len(source_ids), 21)
        self.assertEqual(
            set(self.policy["principle_source"]["approved_principle_ids"]),
            source_ids,
        )
        self.assertEqual(
            self.policy["principle_source"]["source_notes_status"],
            "frozen_v1",
        )
        self.assertEqual(self.policy["status"], "frozen_v1")
        self.assertEqual(
            self.policy["freeze_record"]["approved_principle_count"],
            21,
        )
        self.assertEqual(
            self.policy["freeze_record"]["release_status"],
            "not_released_pending_blind_review_and_uat",
        )

        referenced_ids: set[str] = set()

        def collect_source_principles(value: object) -> None:
            if isinstance(value, dict):
                principles = value.get("source_principles", [])
                if isinstance(principles, list):
                    referenced_ids.update(principles)
                for nested in value.values():
                    collect_source_principles(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect_source_principles(nested)

        collect_source_principles(self.policy)
        self.assertEqual(referenced_ids, source_ids)

    def test_policy_distinguishes_design_objectives_and_rule_origins(
        self,
    ) -> None:
        objectives = {
            item["id"]: item
            for item in self.policy["product_boundary"]["design_objectives"]
        }
        self.assertEqual(
            objectives["natural_expression"]["validation_status"],
            "pending_blind_review",
        )
        self.assertNotIn("claims_allowed", self.policy["product_boundary"])
        self.assertEqual(
            self.policy["source_contamination_metadata"]["rule_origin"],
            "source_governance",
        )
        self.assertEqual(
            self.policy["release_gate"]["rule_origin"],
            "release_evidence",
        )

    def test_required_policy_markers_are_present_in_renderer_prompt(
        self,
    ) -> None:
        prompt_contract = self.policy["prompt_contract"]
        self.assertEqual(
            prompt_contract["implementation_path"],
            "backend/app/agents/interviewer_agent.py",
        )
        markers = prompt_contract["required_markers"]
        self.assertTrue(markers)
        self.assertEqual(
            len({item["id"] for item in markers}),
            len(markers),
        )
        frozen_semantic_equivalents = {
            "active_plan_question": "不得遗漏 validated_plan 指定的表达任务",
        }
        for marker in markers:
            with self.subTest(marker=marker["id"]):
                expected_text = frozen_semantic_equivalents.get(
                    marker["id"],
                    marker["text"],
                )
                self.assertIn(expected_text, self.interviewer_agent_source)
                self.assertTrue(marker["source_principles"])

    def test_source_ledger_is_locatable_bounded_and_risk_labeled(
        self,
    ) -> None:
        required_fields = {
            "source_id",
            "claim",
            "source_name",
            "source_url",
            "source_locator",
            "original_excerpt",
            "reviewed_translation",
            "verification_status",
            "risk_note",
            "product_disposition",
        }
        allowed_statuses = {
            "verified_verbatim",
            "verified_translation",
            "editorial_paraphrase",
            "unsupported_addition",
        }
        source_ids = [row["source_id"] for row in self.source_ledger]
        self.assertEqual(len(source_ids), len(set(source_ids)))
        self.assertEqual(
            {row["verification_status"] for row in self.source_ledger},
            allowed_statuses,
        )

        for row in self.source_ledger:
            with self.subTest(source_id=row["source_id"]):
                self.assertEqual(set(row), required_fields)
                self.assertTrue(row["source_url"].startswith("https://"))
                self.assertTrue(row["source_locator"])
                self.assertTrue(row["risk_note"])
                self.assertTrue(row["product_disposition"])
                if row["verification_status"] == "unsupported_addition":
                    self.assertIsNone(row["original_excerpt"])
                    self.assertIsNone(row["reviewed_translation"])
                else:
                    self.assertTrue(row["original_excerpt"])
                    self.assertTrue(row["reviewed_translation"])
                    self.assertLessEqual(
                        len(row["original_excerpt"].split()),
                        8,
                    )

        unsupported_claims = {
            row["claim"]
            for row in self.source_ledger
            if row["verification_status"] == "unsupported_addition"
        }
        self.assertEqual(
            unsupported_claims,
            SOURCE_CONTAMINATION_BLOCKS,
        )

    def test_pilot_context_manifest_is_locked_to_48_with_32_8_8_split(
        self,
    ) -> None:
        self.assertEqual(len(self.contexts), 48)
        self.assertEqual(
            Counter(row["split"] for row in self.contexts),
            Counter({"train": 32, "dev": 8, "locked_test": 8}),
        )
        self.assertEqual(
            Counter(row["category"] for row in self.contexts),
            Counter(
                {
                    "opening": 4,
                    "probe": 12,
                    "event": 12,
                    "clarify": 6,
                    "repair": 6,
                    "integrate_close": 8,
                }
            ),
        )
        self.assertEqual(
            Counter(
                row["frozen_plan"]["target_dimension"]
                for row in self.contexts
            ),
            EXPECTED_CONTEXT_DIMENSION_COUNTS,
        )
        for split in ("train", "dev", "locked_test"):
            with self.subTest(split=split):
                self.assertEqual(
                    {
                        row["frozen_plan"]["target_dimension"]
                        for row in self.contexts
                        if row["split"] == split
                    },
                    CANONICAL_DIMENSION_KEYS,
                )

        context_ids = [row["context_id"] for row in self.contexts]
        self.assertEqual(len(context_ids), len(set(context_ids)))
        self.assertEqual(len(self.development_contexts), 40)
        self.assertEqual(len(self.locked_contexts), 8)
        self.assertTrue(
            all(row["split"] != "locked_test" for row in self.development_contexts)
        )
        self.assertTrue(
            all(row["split"] == "locked_test" for row in self.locked_contexts)
        )
        for row in self.contexts:
            with self.subTest(context_id=row["context_id"]):
                self.assertEqual(row["schema_version"], CONTEXT_SCHEMA_VERSION)
                self.assertEqual(row["status"], "frozen_v1")
                self.assertEqual(row["privacy"], "synthetic_no_personal_data")
                protected_fields = set(row["plan_protected_fields"])
                self.assertTrue(
                    {
                        "response_intent",
                        "action",
                        "target_dimension",
                        "delivery_mode",
                        "question_intent",
                    }.issubset(protected_fields)
                )
                self.assertTrue(
                    protected_fields.issubset(row["frozen_plan"])
                )
                self.assertTrue(row["allowed_facts"])
                visible_user_turns = {
                    turn["turn_id"]: turn
                    for turn in row["visible_history"]
                    if turn["speaker"] == "user"
                }
                self.assertIn(row["latest_user_turn_id"], visible_user_turns)
                self.assertEqual(
                    set(row["reflection_review"]["turn_ids"]),
                    set(row["frozen_plan"]["reflection_basis_turn_ids"]),
                )
                self.assertTrue(
                    row["reflection_review"]["unsupported_inferences"]
                )
                if row["category"] == "repair":
                    self.assertFalse(row["formal_answer"])
                    self.assertTrue(
                        any(
                            turn["speaker"] == "ai"
                            for turn in row["visible_history"][:-1]
                        )
                    )
                else:
                    self.assertTrue(row["formal_answer"])

    def test_pilot_contexts_construct_exact_runtime_renderer_inputs(self) -> None:
        for record in self.context_records:
            with self.subTest(context_id=record.context_id):
                runtime_context = build_runtime_context(record)
                blueprint = build_evaluation_blueprint(record)
                payload = build_renderer_input_payload(
                    record,
                    style_version=HUMANISTIC_INTERVIEWER_STYLE,
                )
                self.assertEqual(
                    runtime_context.latest_user_turn.turn_id,
                    record.latest_user_turn_id,
                )
                self.assertEqual(
                    payload["validated_plan"],
                    record.frozen_plan.model_dump(mode="json"),
                )
                self.assertEqual(
                    payload["specified_user_turn"]["turn_id"],
                    record.latest_user_turn_id,
                )
                self.assertEqual(
                    {item["turn_id"] for item in payload["reflection_source_turns"]},
                    set(record.frozen_plan.reflection_basis_turn_ids),
                )
                if record.event_unit is not None:
                    selected_event = next(
                        item
                        for item in blueprint.event_cards
                        if item.event_code == record.frozen_plan.release_event_code
                    )
                    selected_unit = next(
                        item
                        for item in selected_event.presentation_units
                        if item.unit_code == record.frozen_plan.release_unit_code
                    )
                    self.assertEqual(selected_unit.text, record.event_unit.text)
                    self.assertEqual(
                        payload["allowed_facts"],
                        [
                            {
                                "event_code": "counter_evidence",
                                "unit_code": record.event_unit.unit_code,
                                "text": record.event_unit.text,
                            }
                        ],
                    )
                else:
                    self.assertEqual(payload["allowed_facts"], [])

    def test_three_candidate_blind_review_workflow_is_explicitly_pending(
        self,
    ) -> None:
        required_workflow_text = (
            "每个上下文生成三个候选",
            "至少两名评审",
            "候选随机展示",
            "双人评审",
            "human_review",
            "尚未经过正式双人盲评",
        )
        for marker in required_workflow_text:
            with self.subTest(marker=marker):
                self.assertIn(marker, self.readme)

        self.assertIn(
            "two_reviewer_blind_review_complete",
            self.policy["release_gate"]["requires"],
        )
        self.assertFalse(self.policy["release_gate"]["default_enabled"])

        for row in self.examples:
            with self.subTest(example_id=row["example_id"]):
                self.assertEqual(row["status"], "provisional_synthetic")
                self.assertIsNone(row["human_review"])
                self.assertEqual(
                    row["policy_review"]["type"],
                    "author_rule_screen",
                )
                expected_result = (
                    "candidate_expected_pass"
                    if row["polarity"] == "positive"
                    else "candidate_expected_block"
                )
                self.assertEqual(
                    row["policy_review"]["result"],
                    expected_result,
                )

    def test_review_corpus_has_required_positive_and_negative_minimums(
        self,
    ) -> None:
        polarity_counts = Counter(row["polarity"] for row in self.examples)
        self.assertGreaterEqual(polarity_counts["positive"], 30)
        self.assertGreaterEqual(polarity_counts["negative"], 12)
        self.assertEqual(set(polarity_counts), {"positive", "negative"})

        example_ids = [row["example_id"] for row in self.examples]
        self.assertEqual(len(example_ids), len(set(example_ids)))
        context_ids = {row["context_id"] for row in self.development_contexts}
        self.assertTrue(
            all(row["context_id"] in context_ids for row in self.examples)
        )
        locked_ids = {row["context_id"] for row in self.locked_contexts}
        self.assertFalse(
            locked_ids & {row["context_id"] for row in self.examples}
        )
        self.assertEqual(
            set(self.context_manifest["isolation_rules"]),
            {f"ISO-{letter}" for letter in "ABCDEFGH"},
        )
        self.assertEqual(
            self.context_manifest["candidate_generator_status"],
            "pending_before_generation",
        )
        self.assertEqual(
            self.context_manifest["status"],
            "frozen_v1",
        )
        freeze_record = self.context_manifest["freeze_record"]
        self.assertEqual(freeze_record["approved_by_role"], "member_a_psy")
        self.assertEqual(
            set(freeze_record["approved_gate_ids"]),
            {f"FREEZE-{letter}" for letter in "ABCDEFGH"},
        )
        self.assertEqual(
            set(freeze_record["approved_rejection_test_ids"]),
            {f"RJ-{index:02d}" for index in range(1, 13)},
        )
        self.assertFalse(freeze_record["candidate_generation_started"])
        self.assertIn(
            "humanistic_release_evaluator",
            {
                item["artifact_id"]
                for item in self.context_manifest["freeze_artifacts"]
            },
        )

    def test_zero_tolerance_labels_and_hard_codes_are_fully_covered(
        self,
    ) -> None:
        self.assertEqual(
            set(self.policy["negative_labels"]),
            ZERO_TOLERANCE_LABELS,
        )
        self.assertEqual(
            set(self.policy["hard_error_codes"]),
            HARD_ERROR_CODES,
        )

        negative_examples = [
            row for row in self.examples if row["polarity"] == "negative"
        ]
        self.assertEqual(
            {row["negative_label"] for row in negative_examples},
            ZERO_TOLERANCE_LABELS,
        )
        self.assertEqual(
            {row["expected_error_code"] for row in negative_examples},
            HARD_ERROR_CODES,
        )
        for row in negative_examples:
            with self.subTest(example_id=row["example_id"]):
                expected_label = self.policy["hard_error_codes"][
                    row["expected_error_code"]
                ]["negative_label"]
                self.assertEqual(row["negative_label"], expected_label)
                self.assertEqual(
                    self.policy["hard_error_codes"][
                        row["expected_error_code"]
                    ]["severity"],
                    "block",
                )

    def test_unsupported_source_additions_are_quarantined_from_runtime_assets(
        self,
    ) -> None:
        self.assertEqual(
            set(self.policy["source_contamination_blocks"]),
            SOURCE_CONTAMINATION_BLOCKS,
        )

        for phrase in SOURCE_CONTAMINATION_BLOCKS:
            matching_audit_lines = [
                line
                for line in self.source_audit.splitlines()
                if phrase in line
            ]
            with self.subTest(phrase=phrase):
                self.assertTrue(matching_audit_lines)
                self.assertTrue(
                    all(
                        "`unsupported_addition`" in line
                        and "禁止进入 Prompt/Few-shot" in line
                        for line in matching_audit_lines
                    )
                )

        prompt_paths = [
            REPO_ROOT / "backend" / "seeds" / "prompts.yaml",
            *sorted(
                (REPO_ROOT / "backend" / "app" / "agents").glob(
                    "*prompt*.py"
                )
            ),
            REPO_ROOT / "backend" / "app" / "agents" / "interviewer_agent.py",
            REPO_ROOT
            / "backend"
            / "app"
            / "agents"
            / "consultative_turn_agent.py",
        ]
        runtime_prompt_text = "\n".join(
            path.read_text(encoding="utf-8") for path in prompt_paths
        )
        few_shot_text = "\n".join(
            (
                DEVELOPMENT_CONTEXTS_PATH.read_text(encoding="utf-8"),
                EXAMPLES_PATH.read_text(encoding="utf-8"),
            )
        )

        principle_policy = copy.deepcopy(self.policy)
        principle_policy.pop("source_contamination_blocks")
        principle_text = _serialized(principle_policy)

        forbidden_runtime_phrases = (
            SOURCE_CONTAMINATION_BLOCKS | OTHER_UNSUPPORTED_SOURCE_PHRASES
        )
        for phrase in forbidden_runtime_phrases:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, runtime_prompt_text)
                self.assertNotIn(phrase, few_shot_text)
                self.assertNotIn(phrase, principle_text)

        self.assertNotIn("Gloria", few_shot_text)
        self.assertNotIn("Pammy", few_shot_text)
        self.assertNotIn("罗杰斯", few_shot_text)


if __name__ == "__main__":
    unittest.main()

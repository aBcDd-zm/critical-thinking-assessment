from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

from app.agents.interview_question_validator import (
    HUMANISTIC_SAFETY_ERROR_CODES,
    RENDERER_VALIDATION_ERROR_CODES,
    InterviewQuestionValidator,
)
from app.agents.progressive_schemas import (
    InterviewQualityFlags,
    InterviewerOutput,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = (
    REPO_ROOT
    / "docs"
    / "humanistic_interviewer"
    / "humanistic_style_policy_v1.yaml"
)
HUMANISTIC_SAFETY_CODES = set(HUMANISTIC_SAFETY_ERROR_CODES)
HUMANISTIC_STYLE_VALIDATION_CODES = {
    "evaluative_praise",
    "overclaimed_understanding",
    "agreement_pressure",
    "corrective_instruction",
    "forced_resolution",
    "plan_question_omission",
}


class HumanisticInterviewerSafetyTests(unittest.TestCase):
    def test_policy_error_code_contract_matches_runtime(self) -> None:
        policy = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            set(policy["hard_error_codes"]),
            HUMANISTIC_SAFETY_ERROR_CODES,
        )
        self.assertEqual(
            set(policy["validation_error_codes"]["codes"]),
            RENDERER_VALIDATION_ERROR_CODES,
        )

    def test_each_boundary_has_a_stable_error_code(self) -> None:
        cases = {
            "你其实不是在担心进度，而是在害怕失败。你会怎么选？":
                "unsupported_hidden_meaning",
            "我感到此刻和你很亲近。你愿意继续说吗？":
                "relational_attachment",
            "你可以把我当成你的父亲。你想从哪里说起？":
                "role_substitution",
            "我也经历过同样的事情。你愿意谈谈吗？":
                "fabricated_self_disclosure",
            "听我的，你应该马上接受这个方案。":
                "prescriptive_authority",
            "我是你的心理咨询师，我会帮助你疗愈。":
                "clinical_role_claim",
        }

        for message, expected_code in cases.items():
            with self.subTest(message=message):
                errors = InterviewQuestionValidator.message_errors(message)
                self.assertIn(expected_code, errors)

    def test_procedural_first_person_is_allowed(self) -> None:
        cases = (
            "我可能没有理解准确，你更在意进度还是质量？",
            "我能提供的信息有限，你会先核实哪一项？",
            "如果我理解有偏差，请纠正：你更担心的是返工风险，对吗？",
            "我想确认一下，你刚才说的是交付时间还是验收标准？",
        )

        for message in cases:
            with self.subTest(message=message):
                errors = InterviewQuestionValidator.message_errors(message)
                self.assertFalse(HUMANISTIC_SAFETY_CODES.intersection(errors))

    def test_ordinary_relationship_and_professional_mentions_are_allowed(self) -> None:
        cases = (
            "你父亲对这项安排提出了什么具体依据？",
            "你会如何向项目咨询顾问说明这项风险？",
            "团队成员其实已经提交了两份记录，你会先核实哪份？",
            "如果持续失眠影响生活，可以考虑向专业心理咨询师求助。",
        )

        for message in cases:
            with self.subTest(message=message):
                errors = InterviewQuestionValidator.message_errors(message)
                self.assertFalse(HUMANISTIC_SAFETY_CODES.intersection(errors))

    def test_adjacent_variants_are_categorized(self) -> None:
        cases = {
            "你内心深处真正想要的是得到所有人的认可。":
                "unsupported_hidden_meaning",
            "你让我非常感动，我会一直陪着你。":
                "relational_attachment",
            "我愿意成为你的朋友，你可以依靠我。":
                "role_substitution",
            "这让我想起我小时候的一段经历。":
                "fabricated_self_disclosure",
            "正确的做法是听我的，就按这个方案执行。":
                "prescriptive_authority",
            "我们现在正在进行心理治疗。":
                "clinical_role_claim",
        }

        for message, expected_code in cases.items():
            with self.subTest(message=message):
                errors = InterviewQuestionValidator.message_errors(message)
                self.assertIn(expected_code, errors)

    def test_measurement_style_violations_have_stable_codes(self) -> None:
        cases = {
            "你的回答非常成熟。你还考虑了什么？": "evaluative_praise",
            "我完全理解你的处境。你准备怎么决定？":
                "overclaimed_understanding",
            "你也同意这是唯一合理方案吧？": "agreement_pressure",
            "你的理解偏了，我来告诉你正确思路。":
                "corrective_instruction",
            "不要再犹豫，现在选一个。": "forced_resolution",
        }

        for message, expected_code in cases.items():
            with self.subTest(message=message):
                errors = InterviewQuestionValidator.message_errors(message)
                self.assertIn(expected_code, errors)

    def test_measurement_style_rules_are_scoped_to_humanistic_mode(self) -> None:
        message = "你的回答非常成熟。你还考虑了什么？"
        errors = InterviewQuestionValidator.message_errors(
            message,
            enforce_humanistic_safety=False,
        )
        self.assertNotIn("evaluative_praise", errors)

    def test_plan_question_omission_is_structurally_blocked(self) -> None:
        output = InterviewerOutput(
            message="我在听。",
            message_type="followup",
            question_count=0,
            quality_flags=InterviewQualityFlags(
                single_focus=True,
                faithful_reflection=True,
                non_judgmental=True,
                non_leading=True,
                no_internal_terms=True,
                no_unreleased_facts=True,
            ),
        )
        valid, errors = InterviewQuestionValidator().validate(
            output,
            plan=SimpleNamespace(
                action="PROBE",
                delivery_mode="plain",
                reflection_basis_turn_ids=[],
            ),
            allowed_fact_codes=set(),
            previous_questions=[],
        )
        self.assertFalse(valid)
        self.assertIn("question_count", errors)
        self.assertIn("plan_question_omission", errors)

    def test_new_style_patterns_do_not_trigger_inside_verified_user_quotes(
        self,
    ) -> None:
        cases = (
            "你的回答非常成熟",
            "我完全理解你的处境",
            "你也同意这是唯一合理方案吧",
            "你的理解偏了",
            "不要再犹豫",
        )
        for quote in cases:
            with self.subTest(quote=quote):
                authored = InterviewQuestionValidator._assistant_authored_text(  # noqa: SLF001
                    f"你刚才提到“{quote}”。这句话依据什么？",
                    [quote],
                )
                errors = InterviewQuestionValidator.message_errors(authored)
                self.assertFalse(
                    HUMANISTIC_STYLE_VALIDATION_CODES.intersection(errors)
                )

    def test_verified_user_quotes_do_not_become_interviewer_claims(self) -> None:
        cases = (
            ("我很害怕项目失败", "你刚才提到“我很害怕项目失败”。你会先核实什么？"),
            ("我曾经遇到类似情况", "你刚才提到“我曾经遇到类似情况”。你会比较哪些依据？"),
            ("你应该先上线", "你刚才提到“你应该先上线”。这个判断基于什么事实？"),
        )

        for quote, message in cases:
            with self.subTest(quote=quote):
                authored = InterviewQuestionValidator._assistant_authored_text(  # noqa: SLF001
                    message,
                    [quote],
                )
                errors = InterviewQuestionValidator.message_errors(authored)
                self.assertFalse(HUMANISTIC_SAFETY_CODES.intersection(errors))

        unquoted = InterviewQuestionValidator._assistant_authored_text(  # noqa: SLF001
            "听我的，你应该先上线。",
            ["你应该先上线"],
        )
        self.assertIn(
            "prescriptive_authority",
            InterviewQuestionValidator.message_errors(unquoted),
        )


if __name__ == "__main__":
    unittest.main()

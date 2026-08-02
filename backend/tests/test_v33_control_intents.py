from __future__ import annotations

import unittest

from app.agents.consultative_turn_agent import ConsultativeTurnAgent
from app.agents.interview_planner_agent import InterviewPlannerAgent
from app.agents.user_turn_intent import (
    classify_consultative_control_intent,
    classify_progressive_control_intent,
    classify_user_turn,
)


class V33ControlIntentTests(unittest.TestCase):
    def test_natural_clarification_expressions_are_not_answers(self) -> None:
        cases = (
            "请把问题再说一遍。",
            "能不能重述一下刚才的问题？",
            "麻烦重复一下题目。",
            "你能把刚才的问题再说一次吗？",
            "我没太明白，能不能换个说法？",
            "什么意思？",
            "我没听懂，你能再解释一下吗？",
            "这个怎么理解？",
            "具体说说",
            "能展开说说吗？",
            "你在说什么？",
            "你刚才这句话是指什么？",
            "你说的“这两边你想先比较什么？”",
            "我没跟上，你在问哪件事？",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(
                    classify_user_turn(text),
                    "clarification_request",
                )
                self.assertEqual(
                    classify_consultative_control_intent(text),
                    "clarify_question",
                )
                self.assertEqual(
                    ConsultativeTurnAgent._repair_intent(text),
                    "clarify_question",
                )

    def test_term_question_uses_term_explanation_route(self) -> None:
        cases = (
            "灰度上线是什么意思？",
            "你说的灰度验证具体是什么意思？",
            "请解释一下灰度上线。",
            "你能说明一下灰度上线吗？",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(
                    classify_user_turn(text),
                    "term_definition_request",
                )
                self.assertEqual(
                    classify_consultative_control_intent(text),
                    "explain_term",
                )

    def test_context_and_repair_keep_existing_routes(self) -> None:
        cases = {
            "现在是什么情况？": "request_context",
            "多给点信息。": "request_context",
            "再说些现在已经知道的情况。": "request_context",
            "还有什么线索吗？": "request_context",
            "能先把现有信息说清楚吗？": "request_context",
            "能再梳理一下目前已有的信息吗？": "request_context",
            "请汇总一下当前线索。": "request_context",
            "可以回顾一下已知情况吗？": "request_context",
            "这个问题刚才已经问过了": "conversation_repair",
            "我说了先召集大家开会": "conversation_repair",
            "都说过先核实分工": "conversation_repair",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(
                    classify_progressive_control_intent(text),
                    expected,
                )
                self.assertEqual(
                    classify_consultative_control_intent(text),
                    expected,
                )

    def test_v11_low_information_route_covers_common_uncertainty_phrases(
        self,
    ) -> None:
        cases = (
            "我不知道",
            "我不确定",
            "还不知道",
            "不太清楚",
            "没想好",
            "没想清楚",
            "说不上来",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(classify_user_turn(text), "low_information")
                self.assertIsNone(ConsultativeTurnAgent._repair_intent(text))
                self.assertEqual(
                    ConsultativeTurnAgent._repair_intent(
                        text,
                        include_low_information=True,
                    ),
                    "low_information",
                )

    def test_substantive_answers_are_not_blocked(self) -> None:
        cases = (
            "所有人的",
            "每个组员",
            "全部负责人",
            "组员们",
            "所有任务",
            "任务清单",
            "我会先确认最影响进度的任务，再根据返工情况调整分工。",
            "我会重复测试关键流程，再决定是否上线。",
            "我会重复验证关键数据，再决定是否上线。",
            "我会重新检查日志，并再次测试弱网场景。",
            "我会让研发重复执行三次复现流程，确认失败率。",
            "我可以重复测试一下吗？",
            (
                "最终方案是交付一个经过完整验收的最小主流程。"
                "代价是部分功能延期，但换来可解释的质量和更低的整体返工风险；"
                "若第三天数据仍显示无法达标，我会提前公开证据并请求进一步减范围或延期。"
            ),
            "我会请负责人解释一下数据差异，再决定是否调整上线范围。",
            "我会具体说说每个人的任务分工。",
            "你说的方案我同意，接下来先查进度。",
            "大家都负责什么，我会一个个核实。",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(
                    classify_user_turn(text),
                    "substantive_answer",
                )
                self.assertIsNone(classify_consultative_control_intent(text))

    def test_meta_clarification_is_not_extracted_as_a_fake_term(self) -> None:
        cases = (
            "我知道，我问的是这句话什么意思。",
            "不是问答案，我是没懂你刚才的问题。",
            "我问的是你上一句在问什么。",
            "这句话具体是什么意思？",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(classify_user_turn(text), "clarification_request")
                self.assertEqual(
                    classify_consultative_control_intent(text),
                    "clarify_question",
                )

    def test_planner_does_not_use_answer_length_as_information_quality(self) -> None:
        for text in (
            "所有人的",
            "每个组员",
            "全部负责人",
            "组员们",
            "任务清单",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    InterviewPlannerAgent._intent(text),
                    "assess_answer",
                )

    def test_humanistic_boundary_requests_are_non_scoring_redirects(self) -> None:
        cases = (
            "你可以当我的心理咨询师吗？",
            "你能做我的父亲吗？",
            "你作为专家直接告诉我选哪个。",
            "如果是你会选哪个？",
            "你告诉我标准答案应该怎么选。",
            "请给我正确答案。",
            "AI直接说出参考答案是什么。",
            "你自己以前经历过这种事情吗？",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(
                    classify_consultative_control_intent(text),
                    "boundary_redirect",
                )
                self.assertEqual(
                    ConsultativeTurnAgent._repair_intent(text),
                    "boundary_redirect",
                )
                self.assertEqual(
                    InterviewPlannerAgent._intent(text),
                    "redirect",
                )

    def test_ordinary_role_mentions_remain_substantive(self) -> None:
        cases = (
            "我会先询问父亲提出这个意见的依据。",
            "团队可以请外部心理咨询师审核员工援助资料。",
            "项目咨询顾问建议先核实数据，我会比较他的依据。",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertIsNone(classify_consultative_control_intent(text))

    def test_unified_control_route_reaches_planner(self) -> None:
        cases = {
            "什么意思？": "clarify_question",
            "你说的灰度验证具体是什么意思？": "explain_term",
            "现在有哪些信息是已经确定的？": "request_context",
            "你刚才问的是证据来源，还是最终方案？": "clarify_question",
            "这个问题刚才已经问过了": "conversation_repair",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(
                    classify_consultative_control_intent(text),
                    expected,
                )
                self.assertEqual(
                    ConsultativeTurnAgent._repair_intent(text),
                    expected,
                )
                self.assertEqual(
                    InterviewPlannerAgent._intent(text),
                    expected,
                )

    def test_planner_retains_substantive_answer_route(self) -> None:
        cases = (
            "我倾向先小范围试用，同时保留人工复核。",
            "我会重复测试关键流程，再决定是否上线。",
            "我会重新检查日志，并再次测试弱网场景。",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertIsNone(classify_consultative_control_intent(text))
                self.assertEqual(
                    InterviewPlannerAgent._intent(text),
                    "assess_answer",
                )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from app.agents.interview_blueprint import build_blueprint_from_generated
from app.agents.consultative_turn_agent import ConsultativeTurnAgent
from app.agents.humanistic_interviewer_v11 import build_v11_microstructure
from app.agents.humanistic_interviewer_v11 import normalize_spoken_focus
from app.agents.progressive_schemas import (
    InterviewPlanOutput,
    InterviewQualityFlags,
    InterviewerOutput,
    PlannerBudget,
)
from app.agents.runtime_interviewer_agent import (
    BASELINE_INTERVIEWER_STYLE,
    EVENT_INTRO_FRAME_EVIDENCE,
    EVENT_INTRO_FRAME_SUPPLEMENT,
    HUMANISTIC_INTERVIEWER_STYLE,
    HUMANISTIC_INTERVIEWER_STYLE_V1_1,
    InterviewerAgent,
)
from app.agents.schemas import (
    AgentRuntimeContext,
    DialogueTurnContext,
    ParticipantContext,
    ScenarioContext,
    SessionContext,
    StageContext,
)
from app.services.occupation_skeleton_service import OccupationSkeletonService


def _blueprint():
    generated = OccupationSkeletonService._build_generated(  # noqa: SLF001
        "测试协作任务",
        "参与者",
        concrete_v33=True,
    )
    return build_blueprint_from_generated(
        generated,
        occupation_category="学生",
        occupation="大学生",
        user_role="参与者",
        task_domain="测试协作任务",
        skeleton_v3_3=True,
        **OccupationSkeletonService._arrangements("测试协作任务"),  # noqa: SLF001
    )


def _context(
    text: str,
    *,
    previous_ai: str | None = None,
) -> AgentRuntimeContext:
    latest = DialogueTurnContext(
        turn_id=91,
        turn_index=2,
        stage_id=11,
        stage_code="s1_problem_definition",
        speaker="user",
        content=text,
        content_type="interview_answer",
    )
    history = []
    if previous_ai:
        history.append(
            DialogueTurnContext(
                turn_id=90,
                turn_index=1,
                stage_id=11,
                stage_code="s1_problem_definition",
                speaker="ai",
                content=previous_ai,
                content_type="interview_followup",
            )
        )
    history.append(latest)
    return AgentRuntimeContext(
        session=SessionContext(
            session_id=7,
            session_uuid=str(uuid4()),
            assessment_mode="mock",
            status="in_progress",
        ),
        participant=ParticipantContext(
            participant_id=3,
            nickname="运行测试",
        ),
        scenario=ScenarioContext(
            scenario_id=10,
            scenario_code="humanistic-v11-test",
            title="测试协作任务",
            background="五天后需要完成一项协作任务。",
        ),
        stage=StageContext(
            stage_id=11,
            stage_code="s1_problem_definition",
            stage_order=1,
            title="界定问题",
            stage_goal="观察问题界定",
            context="当前完成度和质量还没核实。",
            main_question="你会先确认什么？",
            max_followups=2,
        ),
        dialogue_history=history,
        latest_user_turn=latest,
    )


def _plan(
    *,
    action: str = "PROBE",
    delivery_mode: str = "reflective_probe",
    response_intent: str = "assess_answer",
    release_event_code: str | None = None,
    release_unit_code: str | None = None,
    target_dimension: str | None = "evidence_evaluation",
    target_evidence: str | None = "说明一项需要核实的信息",
    active_topic: str = "信息核实",
    question_intent: str = "询问用户会先核实哪一类信息",
) -> InterviewPlanOutput:
    return InterviewPlanOutput(
        response_intent=response_intent,
        action=action,
        active_topic=active_topic,
        target_dimension=(
            target_dimension
            if action in {"PROBE", "CHALLENGE"}
            else target_dimension
            if action == "CLARIFY"
            else None
        ),
        target_evidence=target_evidence,
        delivery_mode=delivery_mode,
        question_intent=question_intent,
        reflection_basis_turn_ids=[91],
        reason="固定计划用于 v1.1 表达层测试",
        release_event_code=release_event_code,
        release_unit_code=release_unit_code,
        budget=PlannerBudget(
            used_turns=1,
            remaining_turns=9,
            reserved_update_turns=2,
            reserved_closure_turns=1,
        ),
    )


class HumanisticInterviewerV11Tests(unittest.TestCase):
    def test_complex_only_polish_is_reserved_for_complex_grounded_bridges(
        self,
    ) -> None:
        agent = InterviewerAgent()
        single = agent.runtime_renderer_input_payload(
            _context("我会先核实数据再决定。"),
            _blueprint(),
            _plan(),
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )
        summary = agent.runtime_renderer_input_payload(
            _context("我会先核实数据再决定。"),
            _blueprint(),
            _plan(delivery_mode="summary_check"),
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )
        double = agent.runtime_renderer_input_payload(
            _context("一方面想赶进度，另一方面担心返工。"),
            _blueprint(),
            _plan(),
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )

        self.assertFalse(agent.v11_requires_model_polish(single, mode="complex_only"))
        self.assertTrue(agent.v11_requires_model_polish(summary, mode="complex_only"))
        self.assertTrue(agent.v11_requires_model_polish(double, mode="complex_only"))
        self.assertFalse(agent.v11_requires_model_polish(double, mode="off"))
        self.assertTrue(agent.v11_requires_model_polish(single, mode="always"))

    def test_adaptive_polish_covers_every_user_facing_followup_action(
        self,
    ) -> None:
        agent = InterviewerAgent()
        context = _context("我会先核实数据再决定。")
        for action, response_intent in (
            ("PROBE", "assess_answer"),
            ("CHALLENGE", "assess_answer"),
            ("CLARIFY", "low_information"),
            ("INTEGRATE", "assess_answer"),
        ):
            with self.subTest(action=action):
                plan = _plan(
                    action=action,
                    response_intent=response_intent,
                    delivery_mode=(
                        "clarification"
                        if action == "CLARIFY"
                        else "integration"
                        if action == "INTEGRATE"
                        else "reflective_probe"
                    ),
                    target_dimension=(
                        "evidence_evaluation"
                        if action in {"PROBE", "CHALLENGE", "CLARIFY"}
                        else None
                    ),
                )
                payload = agent.runtime_renderer_input_payload(
                    context,
                    _blueprint(),
                    plan,
                    style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
                )
                self.assertTrue(
                    agent.v11_requires_model_polish(
                        payload,
                        mode="adaptive",
                    )
                )

    def test_runtime_payload_keeps_bounded_visible_dialogue_and_full_focus(
        self,
    ) -> None:
        prior = "当前你最想先确认哪一点？"
        agent = InterviewerAgent()
        context = _context(
            "为什么会有延迟，是谁负责的",
            previous_ai=prior,
        )
        payload = agent.runtime_renderer_input_payload(
            context,
            _blueprint(),
            _plan(),
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )

        self.assertEqual(payload["latest_user_text"], context.latest_user_turn.content)
        self.assertEqual(payload["preceding_interviewer_message"], prior)
        self.assertEqual(len(payload["recent_visible_dialogue"]), 2)
        self.assertEqual(
            payload["reflection_source_quotes"][0]["quote"],
            "为什么会有延迟，是谁负责的",
        )

    def test_low_information_and_confusion_fallbacks_do_not_shift_work_to_user(
        self,
    ) -> None:
        agent = InterviewerAgent()
        for response_intent, text in (
            ("low_information", "我不知道"),
            ("clarify_question", "没看懂"),
        ):
            with self.subTest(response_intent=response_intent):
                plan = _plan(
                    action="CLARIFY",
                    delivery_mode="clarification",
                    response_intent=response_intent,
                    target_dimension=None,
                    target_evidence=None,
                    question_intent="用更容易回答的方式承接用户",
                )
                context = _context(
                    text,
                    previous_ai="你会先核实哪项信息？",
                )
                output = agent._fallback(  # noqa: SLF001
                    plan,
                    _blueprint(),
                    context,
                    style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
                )
                self.assertNotIn("换一种说法", output.message)
                self.assertNotIn("从最确定的一点", output.message)
                self.assertEqual(output.message.count("？"), 1)
                if response_intent == "clarify_question":
                    payload = agent.runtime_renderer_input_payload(
                        context,
                        _blueprint(),
                        plan,
                        style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
                    )
                    self.assertIn("核实哪项信息", output.message)
                    self.assertEqual(
                        agent._v11_contract_errors(  # noqa: SLF001
                            output,
                            payload,
                        ),
                        [],
                    )
                    off_topic = output.model_copy(update={"message": "你现在最想确认什么？"})
                    self.assertIn(
                        "missing_clarified_question_grounding",
                        agent._v11_contract_errors(  # noqa: SLF001
                            off_topic,
                            payload,
                        ),
                    )

    def test_substantive_short_answer_keeps_its_topic_in_the_next_question(
        self,
    ) -> None:
        agent = InterviewerAgent()
        plan = _plan(
            action="CLARIFY",
            delivery_mode="clarification",
            response_intent="low_information",
            target_dimension=None,
            target_evidence=None,
            question_intent="用更容易回答的方式承接用户",
        )
        context = _context(
            "大家的分工",
            previous_ai="眼下最想确认的是哪一点？",
        )
        output = agent._fallback(  # noqa: SLF001
            plan,
            _blueprint(),
            context,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )
        payload = agent.runtime_renderer_input_payload(
            context,
            _blueprint(),
            plan,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )

        self.assertIn("大家的分工", output.message)
        self.assertEqual(
            agent._v11_contract_errors(output, payload),  # noqa: SLF001
            [],
        )
        generic = output.model_copy(update={"message": "现在眼前哪一点是你比较确定的？"})
        self.assertIn(
            "missing_low_information_topic_grounding",
            agent._v11_contract_errors(generic, payload),  # noqa: SLF001
        )

    def test_clarification_can_preserve_meaning_without_copying_the_old_wording(
        self,
    ) -> None:
        agent = InterviewerAgent()
        plan = _plan(
            action="CLARIFY",
            delivery_mode="clarification",
            response_intent="clarify_question",
            target_dimension=None,
            target_evidence=None,
            question_intent="用具体日常语言解释前一个问题",
        )
        context = _context(
            "没看懂",
            previous_ai="这项安排会直接影响谁？",
        )
        payload = agent.runtime_renderer_input_payload(
            context,
            _blueprint(),
            plan,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )
        natural = agent._fallback(  # noqa: SLF001
            plan,
            _blueprint(),
            context,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )

        self.assertEqual(
            natural.message,
            "我换个更直接的问法：除了你自己，谁的工作会跟着调整？",
        )
        self.assertEqual(
            agent._v11_contract_errors(natural, payload),  # noqa: SLF001
            [],
        )
        stock = natural.model_copy(
            update={"message": "刚才问的是，除了你自己，谁的工作会跟着调整？"}
        )
        self.assertIn(
            "repetitive_stock_phrase",
            agent._v11_contract_errors(stock, payload),  # noqa: SLF001
        )

    def test_constraint_clarification_explains_the_choice_instead_of_reasking_user(
        self,
    ) -> None:
        agent = InterviewerAgent()
        plan = _plan(
            action="CLARIFY",
            delivery_mode="clarification",
            response_intent="clarify_question",
            target_dimension=None,
            target_evidence=None,
            question_intent="澄清并重述当前问题",
        )
        preceding = (
            "新安排是减少交接和检查，原安排是逐项交接检查，"
            "也可只在非关键部分试用；面对这项限制，你会依据什么作出初步决定？"
        )
        context = _context("什么意思", previous_ai=preceding)
        output = agent._fallback(  # noqa: SLF001
            plan,
            _blueprint(),
            context,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )
        payload = agent.runtime_renderer_input_payload(
            context,
            _blueprint(),
            plan,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )

        self.assertIn("新安排", output.message)
        self.assertIn("原安排", output.message)
        self.assertNotIn("换一种说法", output.message)
        self.assertEqual(
            agent._v11_contract_errors(output, payload),  # noqa: SLF001
            [],
        )
        for bad in (
            "我可能没有问清楚。你能换一种说法说明刚才的意思吗？",
            "我可能没有理解准确，你愿意换一种说法吗？",
            "刚才是在问，面对这三种选择，你会根据什么作决定？",
        ):
            with self.subTest(bad=bad):
                rejected = output.model_copy(update={"message": bad})
                self.assertIn(
                    "repetitive_stock_phrase",
                    agent._v11_contract_errors(rejected, payload),  # noqa: SLF001
                )

    def test_two_sided_clarification_explains_the_referent_instead_of_looping(
        self,
    ) -> None:
        preceding = (
            "现有信息还不能说明延迟原因。"
            "为了比较团队里的不同顾虑，我补充一条新的参与者信息："
            "一部分参与者想减少交接和检查以赶进度，另一部分担心返工和质量风险；"
            "这两边先比较什么？"
        )
        plan = _plan(
            action="CLARIFY",
            delivery_mode="clarification",
            response_intent="clarify_question",
            target_dimension=None,
            target_evidence=None,
            question_intent="直接解释上一句的具体对象和选择",
        )
        context = _context("具体说说", previous_ai=preceding)
        agent = InterviewerAgent()
        output = agent._fallback(  # noqa: SLF001
            plan,
            _blueprint(),
            context,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )
        payload = agent.runtime_renderer_input_payload(
            context,
            _blueprint(),
            plan,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )

        self.assertIn("一边想赶进度", output.message)
        self.assertIn("另一边担心返工和质量", output.message)
        self.assertNotIn("具体指哪一部分", output.message)
        self.assertNotIn("边界或限制", output.message)
        self.assertEqual(agent._v11_contract_errors(output, payload), [])  # noqa: SLF001

        for bad in (
            "具体说说具体指哪一部分？",
            "这个问题最关键的边界或限制是什么？",
            "简单说，你现在会先确认哪一点？",
        ):
            with self.subTest(bad=bad):
                errors = agent._v11_contract_errors(  # noqa: SLF001
                    output.model_copy(update={"message": bad}),
                    payload,
                )
                self.assertIn("clarification_loop", errors)

    def test_repeated_confusion_recovers_the_last_concrete_ai_question(self) -> None:
        concrete = (
            "为了比较团队里的不同顾虑，我补充一条新的参与者信息："
            "一边想赶进度，另一边担心返工和质量；这两边先比较什么？"
        )
        context = _context("你在说什么")
        latest = context.latest_user_turn
        self.assertIsNotNone(latest)
        context = context.model_copy(
            update={
                "dialogue_history": [
                    DialogueTurnContext(
                        turn_id=87,
                        turn_index=1,
                        stage_id=11,
                        stage_code="s1_problem_definition",
                        speaker="ai",
                        content=concrete,
                        content_type="interview_followup",
                    ),
                    DialogueTurnContext(
                        turn_id=88,
                        turn_index=2,
                        stage_id=11,
                        stage_code="s1_problem_definition",
                        speaker="user",
                        content="具体说说",
                        content_type="interview_answer",
                    ),
                    DialogueTurnContext(
                        turn_id=89,
                        turn_index=3,
                        stage_id=11,
                        stage_code="s1_problem_definition",
                        speaker="ai",
                        content="具体说说具体指哪一部分？",
                        content_type="interview_followup",
                    ),
                    latest,
                ]
            }
        )

        self.assertEqual(
            InterviewerAgent._clarification_source_message(context),  # noqa: SLF001
            concrete,
        )

    def test_meta_clarification_recovers_meaning_instead_of_parsing_a_term(self) -> None:
        preceding = (
            "一部分参与者想减少交接和检查以赶进度，"
            "另一部分担心这样会增加返工和质量风险；"
            "你会先比较进度收益还是返工风险？"
        )
        plan = _plan(
            action="CLARIFY",
            delivery_mode="clarification",
            response_intent="clarify_question",
            target_dimension=None,
            target_evidence=None,
            question_intent="解释上一个问题要比较的两个对象",
        )
        context = _context(
            "我知道，我问的是这句话什么意思。",
            previous_ai=preceding,
        )
        agent = InterviewerAgent()
        output = agent._fallback(  # noqa: SLF001
            plan,
            _blueprint(),
            context,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )

        self.assertIn("一边想赶进度", output.message)
        self.assertIn("另一边担心返工", output.message)
        self.assertNotIn("我知道我是问这", output.message)
        self.assertNotIn("四方意见", output.message)
        self.assertEqual(output.message.count("？"), 1)

    def test_context_request_recaps_released_facts_without_leaking_future_events(
        self,
    ) -> None:
        blueprint = _blueprint()
        opening_unit = blueprint.event_cards[0].presentation_units[0]
        evidence_unit = blueprint.event_cards[1].presentation_units[0]
        context = _context("多给点信息")
        latest = context.latest_user_turn
        self.assertIsNotNone(latest)
        context = context.model_copy(
            update={
                "dialogue_history": [
                    DialogueTurnContext(
                        turn_id=89,
                        turn_index=1,
                        stage_id=11,
                        stage_code="s1_problem_definition",
                        speaker="user",
                        content="我想先确认大家的分工",
                        content_type="interview_answer",
                    ),
                    latest,
                ]
            }
        )
        state = SimpleNamespace(
            released_unit_codes=[opening_unit.unit_code, evidence_unit.unit_code]
        )

        message = ConsultativeTurnAgent._context_message(  # noqa: SLF001
            "多给点信息",
            blueprint,
            state,
            context,
        )

        self.assertIn(opening_unit.text.rstrip("。"), message)
        self.assertIn(evidence_unit.text.rstrip("。"), message)
        self.assertIn("还没有列出每个人的具体分工", message)
        self.assertNotIn("一部分参与者想减少交接", message)
        self.assertIn("先查哪类任务记录", message)
        self.assertNotIn("想先判断哪一点", message)
        self.assertEqual(message.count("？"), 1)

    def test_explicit_arrangement_review_uses_only_static_scenario_envelope(
        self,
    ) -> None:
        blueprint = _blueprint()
        context = _context("新安排和原安排分别是什么")
        state = SimpleNamespace(released_unit_codes=[])

        message = ConsultativeTurnAgent._context_message(  # noqa: SLF001
            context.latest_user_turn.content,
            blueprint,
            state,
            context,
        )

        self.assertIn("这是题目里已给出的方案背景", message)
        self.assertIn("新安排是减少交接和检查", message)
        self.assertIn("原安排是继续逐项交接检查", message)
        self.assertNotIn(blueprint.pilot_arrangement, message)
        self.assertNotIn(blueprint.stakeholder_conflict, message)
        self.assertNotIn("返工比例从5%升到18%", message)
        self.assertEqual(message.count("？"), 1)

    def test_context_recap_is_not_replaced_by_scoring_duplicate_repair(self) -> None:
        blueprint = _blueprint()
        opening_unit = blueprint.event_cards[0].presentation_units[0]
        context = _context("能再汇总一下已知信息吗")
        state = SimpleNamespace(released_unit_codes=[opening_unit.unit_code])
        message = ConsultativeTurnAgent._context_message(  # noqa: SLF001
            context.latest_user_turn.content,
            blueprint,
            state,
            context,
        )
        plan = _plan(
            action="CLARIFY",
            delivery_mode="clarification",
            response_intent="request_context",
            target_dimension=None,
            target_evidence=None,
            question_intent="直接补全用户询问的情境信息",
        ).model_copy(update={"reflection_basis_turn_ids": []})
        output = InterviewerOutput(
            message=message,
            message_type="clarification",
            question_count=1,
            quality_flags=InterviewQualityFlags(
                single_focus=True,
                faithful_reflection=True,
                non_judgmental=True,
                non_leading=True,
                no_internal_terms=True,
                no_unreleased_facts=True,
            ),
        )

        errors = ConsultativeTurnAgent().validate_turn(
            output,
            plan=plan,
            blueprint=blueprint,
            context=context,
            previous_questions=[message],
            state=state,
        )

        self.assertNotIn("duplicate_question", errors)
        self.assertNotIn("semantic_duplicate_question", errors)
        self.assertEqual(errors, [])

    def test_recovery_skips_a_chain_of_meta_rephrases(self) -> None:
        concrete = (
            "一边想赶进度，另一边担心返工和质量；"
            "你想先比较哪方面的影响？"
        )
        latest = _context("你在说什么").latest_user_turn
        self.assertIsNotNone(latest)
        context = _context("你在说什么").model_copy(
            update={
                "dialogue_history": [
                    DialogueTurnContext(
                        turn_id=84,
                        turn_index=1,
                        stage_id=11,
                        stage_code="s1_problem_definition",
                        speaker="ai",
                        content=concrete,
                        content_type="interview_event",
                    ),
                    DialogueTurnContext(
                        turn_id=85,
                        turn_index=2,
                        stage_id=11,
                        stage_code="s1_problem_definition",
                        speaker="user",
                        content="什么意思",
                        content_type="interview_answer",
                    ),
                    DialogueTurnContext(
                        turn_id=86,
                        turn_index=3,
                        stage_id=11,
                        stage_code="s1_problem_definition",
                        speaker="ai",
                        content=(
                            "上一问是：一部分人想赶进度，"
                            "另一部分担心返工；你会先比较哪一边？"
                        ),
                        content_type="interview_clarification",
                    ),
                    DialogueTurnContext(
                        turn_id=87,
                        turn_index=4,
                        stage_id=11,
                        stage_code="s1_problem_definition",
                        speaker="user",
                        content="我知道，我问的是这是什么意思",
                        content_type="interview_answer",
                    ),
                    DialogueTurnContext(
                        turn_id=88,
                        turn_index=5,
                        stage_id=11,
                        stage_code="s1_problem_definition",
                        speaker="ai",
                        content="上一问是：四方意见不一样时，你会先处理谁的担忧？",
                        content_type="interview_clarification",
                    ),
                    latest,
                ]
            }
        )

        self.assertEqual(
            InterviewerAgent._clarification_source_message(context),  # noqa: SLF001
            concrete,
        )

    def test_colloquial_particle_is_not_treated_as_part_of_the_topic(self) -> None:
        self.assertEqual(normalize_spoken_focus("组员呀"), "组员")
        agent = InterviewerAgent()
        plan = _plan(
            action="PROBE",
            delivery_mode="reflective_probe",
            response_intent="low_information",
            target_dimension="problem_definition",
            target_evidence="换一个具体角度继续访谈",
            question_intent="不再重复澄清，换一个容易回答的具体角度",
        )
        context = _context(
            "组员呀",
            previous_ai="除了你自己，还有谁会因为你先看进度而调整工作？",
        )
        output = agent._fallback(  # noqa: SLF001
            plan,
            _blueprint(),
            context,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )
        payload = agent.runtime_renderer_input_payload(
            context,
            _blueprint(),
            plan,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )

        self.assertEqual(output.message, "这些组员里，谁的任务最需要先确认？")
        self.assertNotIn("组员呀", output.message)
        self.assertNotIn("在“", output.message)
        self.assertEqual(
            agent._v11_contract_errors(output, payload),  # noqa: SLF001
            [],
        )

    def test_problem_cause_answer_gets_a_cause_specific_question(self) -> None:
        plan = _plan(
            target_dimension="problem_definition",
            target_evidence="补充当前判断的一项关键依据",
            question_intent="顺着当前话题补充一项尚未充分的证据",
        )
        payload = build_v11_microstructure(
            _context("是谁导致的延迟"),
            plan,
            previous_questions=[],
        )

        self.assertEqual(payload["candidate_intent_key"], "problem_cause_scope")
        self.assertTrue(
            all(
                "延迟" in item["text"]
                and any(word in item["text"] for word in ("查", "看", "核对"))
                for item in payload["question_candidates"]
            )
        )
        division_payload = build_v11_microstructure(
            _context("我想先确认大家分别负责什么"),
            plan,
            previous_questions=[],
        )
        self.assertEqual(division_payload["candidate_intent_key"], "dimension_problem")
        self.assertNotIn("延迟原因", division_payload["selected_question"])

    def test_semantic_anchor_allows_natural_rephrasing_without_keyword_gate(
        self,
    ) -> None:
        agent = InterviewerAgent()
        plan = _plan(
            target_dimension="multiple_perspectives",
            target_evidence="补充当前判断的一项关键依据",
            question_intent="顺着当前话题补充一项尚未充分的证据",
        )
        context = _context(
            "为什么会有延迟，是谁负责的",
            previous_ai="看到这项延迟记录后，你准备先核实什么？",
        )
        output = agent._fallback(  # noqa: SLF001
            plan,
            _blueprint(),
            context,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )
        payload = agent.runtime_renderer_input_payload(
            context,
            _blueprint(),
            plan,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )

        self.assertEqual(
            output.message,
            "好，先查清延迟发生在哪个环节：你会先核对谁的任务或交接？",
        )
        self.assertEqual(
            agent._v11_contract_errors(output, payload),  # noqa: SLF001
            [],
        )
        alternative = str(payload["question_candidates"][1]["text"])
        natural_rephrase = output.model_copy(update={"message": alternative})
        self.assertEqual(
            agent._v11_contract_errors(natural_rephrase, payload),  # noqa: SLF001
            [],
        )

        off_target = output.model_copy(update={"message": "你还想补充什么？"})
        self.assertTrue(
            any(
                item.startswith("surface_semantic_anchor_missing")
                for item in agent._v11_contract_errors(  # noqa: SLF001
                    off_target,
                    payload,
                )
            )
        )

    def test_v11_validation_error_gets_one_equal_budget_repair_attempt(self) -> None:
        agent = InterviewerAgent()
        context = _context("我会先核实数据再决定。")
        blueprint = _blueprint()
        plan = _plan()
        renderer_input = agent.runtime_renderer_input_payload(
            context,
            blueprint,
            plan,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )
        valid_message = str(renderer_input["selected_question"])
        responses = iter(
            (
                (json.dumps({"message": "你还想补充什么？"}, ensure_ascii=False), "deepseek-v4-pro"),
                (json.dumps({"message": valid_message}, ensure_ascii=False), "deepseek-v4-pro"),
            )
        )

        with (
            patch(
                "app.agents.runtime_interviewer_agent.get_settings",
                return_value=SimpleNamespace(MODEL_GATEWAY_MODE="real"),
            ),
            patch.object(agent, "_call", side_effect=lambda *_args, **_kwargs: next(responses)),
        ):
            result = agent.render(
                context,
                blueprint,
                plan,
                previous_questions=[],
                style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
                timeout_seconds=5,
                primary_timeout_seconds=3,
                renderer_input=renderer_input,
            )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.model_attempt_count, 2)
        self.assertEqual(result.retry_reason, "validation_error")
        self.assertEqual(result.output.message, valid_message)

    def test_v11_can_remove_only_a_rejected_stock_preface(self) -> None:
        agent = InterviewerAgent()
        context = _context("我会先核实数据再决定。")
        blueprint = _blueprint()
        plan = _plan()
        renderer_input = agent.runtime_renderer_input_payload(
            context,
            blueprint,
            plan,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )
        valid_message = str(renderer_input["selected_question"])
        raw_message = f"你提到会先核实数据；{valid_message}"

        with (
            patch(
                "app.agents.runtime_interviewer_agent.get_settings",
                return_value=SimpleNamespace(MODEL_GATEWAY_MODE="real"),
            ),
            patch.object(
                agent,
                "_call",
                return_value=(
                    json.dumps({"message": raw_message}, ensure_ascii=False),
                    "deepseek-v4-pro",
                ),
            ) as model_call,
        ):
            result = agent.render(
                context,
                blueprint,
                plan,
                previous_questions=[],
                style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
                timeout_seconds=5,
                primary_timeout_seconds=3,
                renderer_input=renderer_input,
            )

        model_call.assert_called_once()
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.model_attempt_count, 1)
        self.assertEqual(result.output.message, valid_message)
        self.assertIn(
            "rejected stock preface removed; live semantic question retained",
            result.output.warnings,
        )

    def test_v11_transport_failure_uses_general_plan_bound_degradation(self) -> None:
        agent = InterviewerAgent()
        context = _context("我会先核实数据再决定。")
        blueprint = _blueprint()
        plan = _plan()
        renderer_input = agent.runtime_renderer_input_payload(
            context,
            blueprint,
            plan,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )

        with (
            patch(
                "app.agents.runtime_interviewer_agent.get_settings",
                return_value=SimpleNamespace(MODEL_GATEWAY_MODE="real"),
            ),
            patch.object(agent, "_call", side_effect=TimeoutError("synthetic timeout")),
        ):
            result = agent.render(
                context,
                blueprint,
                plan,
                previous_questions=[],
                style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
                timeout_seconds=5,
                primary_timeout_seconds=3,
                renderer_input=renderer_input,
            )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.model_attempt_count, 2)
        self.assertEqual(result.retry_reason, "TimeoutError")
        self.assertEqual(result.output.question_count, 1)
        self.assertIn(
            "general plan-bound graceful degradation",
            result.output.warnings,
        )

    def test_true_uncertainty_does_not_require_echoing_the_answer(self) -> None:
        agent = InterviewerAgent()
        plan = _plan(
            action="CLARIFY",
            delivery_mode="clarification",
            response_intent="low_information",
            target_dimension=None,
            target_evidence=None,
            question_intent="用更容易回答的方式承接用户",
        )
        context = _context("我不知道", previous_ai="你会先确认什么？")
        output = agent._fallback(  # noqa: SLF001
            plan,
            _blueprint(),
            context,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )
        payload = agent.runtime_renderer_input_payload(
            context,
            _blueprint(),
            plan,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )

        self.assertNotIn("我不知道", output.message)
        self.assertNotIn(
            "missing_low_information_topic_grounding",
            agent._v11_contract_errors(output, payload),  # noqa: SLF001
        )
        echoed = output.model_copy(update={"message": "在“我不知道”里，你想先弄清哪一点？"})
        self.assertIn(
            "echoed_uncertainty_as_topic",
            agent._v11_contract_errors(echoed, payload),  # noqa: SLF001
        )

    def test_live_surface_may_rephrase_but_cannot_drop_semantic_anchors(self) -> None:
        agent = InterviewerAgent()
        context = _context("我会先核实数据再决定。")
        plan = _plan()
        payload = agent.runtime_renderer_input_payload(
            context,
            _blueprint(),
            plan,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )
        output = agent._fallback(  # noqa: SLF001
            plan,
            _blueprint(),
            context,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )
        alternative = payload["question_candidates"][1]["text"]
        rephrased = output.model_copy(
            update={
                "message": output.message.replace(
                    payload["selected_question"],
                    alternative,
                )
            }
        )
        self.assertNotIn(payload["selected_question"], rephrased.message)
        self.assertEqual(
            agent._v11_contract_errors(rephrased, payload), []
        )  # noqa: SLF001

        off_target = rephrased.model_copy(
            update={"message": rephrased.message.replace(alternative, "你还想补充什么？")}
        )
        self.assertTrue(
            any(
                item.startswith("surface_semantic_anchor_missing")
                for item in agent._v11_contract_errors(  # noqa: SLF001
                    off_target,
                    payload,
                )
            )
        )

    def test_deterministic_primary_is_success_without_model_call(self) -> None:
        context = _context("我会先核实数据再决定。")
        blueprint = _blueprint()
        plan = _plan()
        agent = InterviewerAgent()
        renderer_input = agent.runtime_renderer_input_payload(
            context,
            blueprint,
            plan,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )
        with (
            patch(
                "app.agents.runtime_interviewer_agent.get_settings",
                return_value=SimpleNamespace(MODEL_GATEWAY_MODE="real"),
            ),
            patch.object(
                agent,
                "_call",
                side_effect=AssertionError("model must not be called"),
            ) as model_call,
        ):
            result = agent.render(
                context,
                blueprint,
                plan,
                previous_questions=[],
                style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
                timeout_seconds=5,
                allow_model_call=False,
                deterministic_primary=True,
                renderer_input=renderer_input,
            )

        model_call.assert_not_called()
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.model_attempt_count, 0)
        self.assertFalse(result.output.fallback_used)
        self.assertTrue(result.audit_metadata["deterministic_primary"])

    def test_legacy_humanistic_v1_fallback_is_unchanged(self) -> None:
        output = InterviewerAgent()._fallback(  # noqa: SLF001
            _plan(),
            _blueprint(),
            _context("我会先核实当前完成度和质量记录，再决定是否减少检查。"),
            style_version=HUMANISTIC_INTERVIEWER_STYLE,
        )

        self.assertEqual(
            output.message,
            "你刚才提到“我会先核实当前完成度和质量记录”。" "为了判断得更稳妥，你会先核实哪一类信息？",
        )
        self.assertEqual(output.warnings, [])

    def test_three_candidates_are_audited_and_full_message_duplicate_is_rejected(
        self,
    ) -> None:
        prior = "你提到“先核实数据”；" "你准备先核实哪类信息，再作判断？"
        payload = InterviewerAgent.runtime_renderer_input_payload(
            _context("我会先核实数据再决定。", previous_ai=prior),
            _blueprint(),
            _plan(),
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )

        candidates = payload["question_candidates"]
        self.assertEqual(len(candidates), 3)
        self.assertFalse(candidates[0]["eligible"])
        self.assertIn("duplicate_question", candidates[0]["validation_codes"])
        self.assertNotEqual(
            payload["selected_candidate_id"],
            candidates[0]["candidate_id"],
        )
        self.assertEqual(
            payload["selection_reason"],
            "highest_novelty_then_stable_order",
        )

    def test_reflection_uses_only_exact_single_or_explicit_double_spans(
        self,
    ) -> None:
        cases = (
            (
                "我会先核实数据再决定。",
                "single",
                1,
            ),
            (
                "一方面想赶进度，另一方面担心返工。",
                "double",
                2,
            ),
            (
                "我既考虑进度也考虑质量。",
                "single",
                1,
            ),
        )
        agent = InterviewerAgent()
        for text, side_type, quote_count in cases:
            with self.subTest(text=text):
                context = _context(text)
                output = agent._fallback(  # noqa: SLF001
                    _plan(),
                    _blueprint(),
                    context,
                    style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
                )
                payload = agent.runtime_renderer_input_payload(
                    context,
                    _blueprint(),
                    _plan(),
                    style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
                )
                self.assertEqual(payload["reflection_side_type"], side_type)
                self.assertEqual(
                    len(payload["reflection_source_quotes"]),
                    quote_count,
                )
                for source in payload["reflection_source_quotes"]:
                    self.assertIn(source["quote"], text)
                self.assertEqual(output.message.count("？"), 1)

    def test_all_ineligible_candidates_use_audited_safe_fallback(self) -> None:
        previous = [
            "你准备先核实哪类信息，再作判断？",
            "现有说法里，哪一点还需要查证后才能相信？",
            "还缺少什么信息，才能排除另一种可能？",
        ]
        audit = build_v11_microstructure(
            _context("我会先核实数据再决定。"),
            _plan(),
            previous_questions=previous,
        )

        self.assertTrue(
            all(not item["eligible"] for item in audit["question_candidates"])
        )
        self.assertEqual(
            audit["selected_candidate_id"],
            "v11_deterministic_safe_fallback",
        )
        self.assertEqual(
            audit["selector_fallback_reason"],
            "all_three_candidates_ineligible",
        )

    def test_summary_check_is_tentative_but_has_only_one_question(self) -> None:
        output = InterviewerAgent()._fallback(  # noqa: SLF001
            _plan(delivery_mode="summary_check"),
            _blueprint(),
            _context("我倾向先延期并核实故障日志。"),
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )

        self.assertTrue(output.message.startswith("我先确认一下：你说的是"))
        self.assertEqual(output.message.count("？") + output.message.count("?"), 1)
        self.assertNotIn("我理解得对吗", output.message)

    def test_mixed_authority_keeps_substantive_quote_and_autonomy_boundary(
        self,
    ) -> None:
        texts = (
            "我倾向延期，因为故障风险高；但你觉得我应该上线还是延期？",
            "你觉得我应该上线还是延期？我倾向延期，因为故障风险高。",
        )
        agent = InterviewerAgent()
        for text in texts:
            with self.subTest(text=text):
                context = _context(text)
                output = agent._fallback(  # noqa: SLF001
                    _plan(),
                    _blueprint(),
                    context,
                    style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
                )
                payload = agent.runtime_renderer_input_payload(
                    context,
                    _blueprint(),
                    _plan(),
                    style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
                )
                self.assertTrue(payload["mixed_authority_request"])
                self.assertIn("决定仍由你依据情境作出", output.message)
                self.assertEqual(output.message.count("？"), 1)
                self.assertTrue(payload["reflection_source_quotes"])
                for source in payload["reflection_source_quotes"]:
                    self.assertIn(source["quote"], text)

    def test_pure_authority_uses_non_scoring_autonomy_support_question(
        self,
    ) -> None:
        context = _context("你觉得我应该上线还是延期？")
        plan = _plan(
            action="CLARIFY",
            delivery_mode="clarification",
            response_intent="redirect",
            target_dimension=None,
            target_evidence=None,
            active_topic="自主判断标准",
            question_intent="说明不能替用户作决定，并邀请用户说明自己的判断标准",
        )
        agent = InterviewerAgent()
        output = agent._fallback(  # noqa: SLF001
            plan,
            _blueprint(),
            context,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )
        payload = agent.runtime_renderer_input_payload(
            context,
            _blueprint(),
            plan,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )

        self.assertEqual(payload["authority_request_kind"], "pure")
        self.assertTrue(payload["pure_authority_request"])
        self.assertEqual(payload["candidate_intent_key"], "pure_authority_criteria")
        self.assertIn("不能替你作出这个决定", output.message)
        self.assertIn("判断标准", output.message)
        self.assertEqual(output.message.count("？"), 1)
        self.assertEqual(payload["reflection_source_quotes"], [])
        self.assertEqual(
            agent._v11_contract_errors(output, payload), []
        )  # noqa: SLF001

    def test_special_planner_intents_bind_all_three_candidates(self) -> None:
        cases = (
            (
                _plan(
                    target_dimension="integrative_decision",
                    target_evidence="形成反向信息前的明确初步决定",
                    active_topic="初步决定",
                    question_intent="在既有安排、减少检查或小范围试用中形成初步决定",
                ),
                "initial_decision",
                ("选择", "初步决定", "倾向"),
            ),
            (
                _plan(
                    target_dimension="reasoning_argumentation",
                    target_evidence="补足结束前仍不充分的维度证据",
                    active_topic="结束前证据补充",
                    question_intent="为尚未充分的维度提供一次新的公平作答机会",
                ),
                "ending_gap_reasoning_argumentation",
                ("依据", "事实", "理由"),
            ),
            (
                _plan(
                    action="CLARIFY",
                    delivery_mode="clarification",
                    response_intent="conversation_repair",
                    target_dimension="evidence_evaluation",
                    target_evidence="从未重复的观察角度补充一项可判断信息",
                    question_intent="承接纠错，改从证据评估角度提出未重复问题",
                ),
                "repair_evidence_evaluation",
                ("没有承接好",),
            ),
        )
        for plan, expected_key, expected_terms in cases:
            with self.subTest(expected_key=expected_key):
                payload = build_v11_microstructure(
                    _context("我会先核实数据再决定。"),
                    plan,
                    previous_questions=[],
                )
                self.assertEqual(payload["candidate_intent_key"], expected_key)
                self.assertEqual(
                    {item["intent_key"] for item in payload["question_candidates"]},
                    {expected_key},
                )
                self.assertTrue(
                    all(
                        any(term in item["text"] for term in expected_terms)
                        for item in payload["question_candidates"]
                    )
                )

    def test_v11_model_may_omit_stock_reflection_but_keeps_source_audit(
        self,
    ) -> None:
        agent = InterviewerAgent()
        context = _context("我会先核实数据再决定。")
        blueprint = _blueprint()
        plan = _plan()
        renderer_input = agent.runtime_renderer_input_payload(
            context,
            blueprint,
            plan,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )
        model_message = renderer_input["selected_question"]
        calls = 0

        def fake_call(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            return (
                json.dumps(
                    {"message": model_message},
                    ensure_ascii=False,
                ),
                "unsafe-v11-renderer",
            )

        with (
            patch(
                "app.agents.runtime_interviewer_agent.get_settings",
                return_value=SimpleNamespace(MODEL_GATEWAY_MODE="real"),
            ),
            patch.object(agent, "_call", side_effect=fake_call),
        ):
            result = agent.render(
                context,
                blueprint,
                plan,
                previous_questions=[],
                template_content="humanistic_compact_v1_1",
                style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
                timeout_seconds=3,
                renderer_input=renderer_input,
            )

        self.assertEqual(calls, 1)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.validation_errors, [])
        self.assertTrue(renderer_input["reflection_source_quotes"])
        self.assertEqual(
            result.audit_metadata["selected_candidate_id"],
            renderer_input["selected_candidate_id"],
        )
        stock = result.output.model_copy(
            update={"message": f"你提到数据；{renderer_input['selected_question']}"}
        )
        self.assertIn(
            "repetitive_stock_phrase",
            agent._v11_contract_errors(stock, renderer_input),  # noqa: SLF001
        )
        service_script = result.output.model_copy(
            update={
                "message": (
                    "刚才的问题是想了解，"
                    f"{renderer_input['selected_question']}"
                )
            }
        )
        self.assertIn(
            "repetitive_stock_phrase",
            agent._v11_contract_errors(  # noqa: SLF001
                service_script,
                renderer_input,
            ),
        )
        evaluative = result.output.model_copy(
            update={
                "message": (
                    "确认分工是第一步，"
                    f"{renderer_input['selected_question']}"
                )
            }
        )
        self.assertIn(
            "evaluative_acknowledgement",
            agent._v11_contract_errors(  # noqa: SLF001
                evaluative,
                renderer_input,
            ),
        )
        unsupported_quote = result.output.model_copy(
            update={"message": f"你担心“团队一定会失败”；{renderer_input['selected_question']}"}
        )
        self.assertIn(
            "unsupported_visible_quote",
            agent._v11_contract_errors(  # noqa: SLF001
                unsupported_quote,
                renderer_input,
            ),
        )
        changed = result.output.model_copy(
            update={
                "message": result.output.message.replace(
                    renderer_input["selected_question"],
                    "你还想补充什么？",
                )
            }
        )
        self.assertTrue(
            any(
                item.startswith("surface_semantic_anchor_missing")
                for item in agent._v11_contract_errors(  # noqa: SLF001
                    changed,
                    renderer_input,
                )
            )
        )
        mismatched = dict(renderer_input)
        mismatched["question_candidates"] = [
            {**item, "intent_key": "wrong_intent"}
            for item in renderer_input["question_candidates"]
        ]
        self.assertIn(
            "candidate_intent_mismatch",
            agent._v11_contract_errors(  # noqa: SLF001
                result.output,
                mismatched,
            ),
        )

    def test_long_release_event_fallback_remains_valid_and_under_limit(
        self,
    ) -> None:
        blueprint = _blueprint()
        event = blueprint.event_cards[1]
        unit = event.presentation_units[0]
        unit.text = (
            "这是一条需要保持原样的较长事件事实，其中包含当前安排的限制、" "相关参与者的意见以及尚未确认的信息来源，可能影响后续决定。" "又补足到接近上限。"
        )[:70]
        plan = _plan(
            action="RELEASE_EVENT",
            delivery_mode="event_link",
            release_event_code=event.event_code,
            release_unit_code=unit.unit_code,
        )
        context = _context("一方面想赶进度，另一方面担心返工风险。")
        agent = InterviewerAgent()
        output = agent._fallback(  # noqa: SLF001
            plan,
            blueprint,
            context,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )
        payload = agent.runtime_renderer_input_payload(
            context,
            blueprint,
            plan,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )

        self.assertLessEqual(len(output.message), 90)
        self.assertIn(unit.text.rstrip("。！？!?"), output.message)
        self.assertEqual(output.message.count("？"), 1)
        self.assertTrue(payload["compact_event_fact"])
        self.assertIn(
            payload["reflection_adjustment_reason"],
            {
                "double_to_single_length_budget",
                "exact_quote_shortened_for_length",
                "omitted_for_length",
            },
        )
        for source in payload["reflection_source_quotes"]:
            self.assertGreaterEqual(len(source["quote"]), 4)
        valid, errors = agent.validator.validate(
            output,
            plan=plan,
            allowed_fact_codes={unit.unit_code},
            previous_questions=[],
            allowed_source_turn_ids={91},
            source_turn_texts={91: context.latest_user_turn.content},
            allowed_fact_text=unit.text,
            enforce_humanistic_safety=True,
        )
        self.assertTrue(valid, errors)
        self.assertEqual(
            agent._v11_contract_errors(output, payload), []
        )  # noqa: SLF001

    def test_release_events_bridge_the_latest_concrete_user_focus(self) -> None:
        blueprint = _blueprint()
        agent = InterviewerAgent()
        cases = (
            ("evidence_uncertainty", "先确认大家的分工", "新的记录信息"),
            ("stakeholder_conflict", "先看每个人的任务完成情况", "新的参与者信息"),
            ("decision_pressure", "我会优先看目前项目进度", "放进具体选择"),
        )
        for event_code, latest, bridge in cases:
            with self.subTest(event_code=event_code):
                event = next(
                    item
                    for item in blueprint.event_cards
                    if item.event_code == event_code
                )
                unit = event.presentation_units[0]
                plan = _plan(
                    action="RELEASE_EVENT",
                    delivery_mode="event_link",
                    release_event_code=event_code,
                    release_unit_code=unit.unit_code,
                )
                context = _context(latest)
                output = agent._fallback(  # noqa: SLF001
                    plan,
                    blueprint,
                    context,
                    style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
                )
                payload = agent.runtime_renderer_input_payload(
                    context,
                    blueprint,
                    plan,
                    style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
                )

                self.assertIn(unit.text.rstrip("。！？!?"), output.message)
                self.assertIn(bridge, output.message)
                self.assertIn("为了", output.message)
                self.assertRegex(output.message, r"补充一[条项]新")
                self.assertLessEqual(len(output.message), 90)
                self.assertEqual(
                    agent._v11_contract_errors(output, payload),  # noqa: SLF001
                    [],
                )

                if event_code == "decision_pressure":
                    awkward = output.model_copy(
                        update={
                            "message": (
                                f"{unit.text.rstrip('。！？!?')}；"
                                "优先看目前项目进度，在这些限制下"
                                "你会依据什么作出初步决定？"
                            )
                        }
                    )
                    self.assertIn(
                        "missing_event_introduction",
                        agent._v11_contract_errors(  # noqa: SLF001
                            awkward,
                            payload,
                        ),
                    )

                if len(unit.text.rstrip("。！？!?")) > 18:
                    truncated = output.model_copy(
                        update={
                            "message": output.message.replace(
                                unit.text.rstrip("。！？!?"),
                                unit.text.rstrip("。！？!?")[:18],
                            )
                        }
                    )
                    self.assertIn(
                        "missing_required_fact_verbatim",
                        agent._v11_contract_errors(truncated, payload),  # noqa: SLF001
                    )

    def test_event_bridge_marks_information_boundary_before_new_conflict(
        self,
    ) -> None:
        blueprint = _blueprint()
        event = next(
            item
            for item in blueprint.event_cards
            if item.event_code == "stakeholder_conflict"
        )
        unit = event.presentation_units[0]
        plan = _plan(
            action="RELEASE_EVENT",
            delivery_mode="event_link",
            release_event_code=event.event_code,
            release_unit_code=unit.unit_code,
        )
        context = _context("为什么会有延迟，是谁负责的")
        agent = InterviewerAgent()
        output = agent._fallback(  # noqa: SLF001
            plan,
            blueprint,
            context,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )
        payload = agent.runtime_renderer_input_payload(
            context,
            blueprint,
            plan,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )

        self.assertIn("现有信息还不能说明延迟原因", output.message)
        self.assertIn("为了比较团队里的不同顾虑", output.message)
        self.assertIn("我补充一条新的参与者信息", output.message)
        self.assertIn(unit.text.rstrip("。！？!?"), output.message)
        self.assertLessEqual(len(output.message), 90)
        self.assertEqual(agent._v11_contract_errors(output, payload), [])  # noqa: SLF001

        abrupt = output.model_copy(
            update={
                "message": (
                    f"{unit.text.rstrip('。！？!?')}；"
                    "你会先比较哪方面的影响？"
                )
            }
        )
        self.assertIn(
            "missing_event_introduction",
            agent._v11_contract_errors(abrupt, payload),  # noqa: SLF001
        )

    def test_counter_evidence_bridges_from_people_before_revising_plan(
        self,
    ) -> None:
        blueprint = _blueprint()
        event = next(
            item for item in blueprint.event_cards if item.event_code == "counter_evidence"
        )
        unit = next(
            item
            for item in event.presentation_units
            if item.unit_code == "error_rate_increase"
        )
        plan = _plan(
            action="RELEASE_EVENT",
            delivery_mode="event_link",
            release_event_code=event.event_code,
            release_unit_code=unit.unit_code,
        )
        context = _context("肯定是组员呀")
        agent = InterviewerAgent()
        output = agent._fallback(  # noqa: SLF001
            plan,
            blueprint,
            context,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )
        payload = agent.runtime_renderer_input_payload(
            context,
            blueprint,
            plan,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )

        self.assertIn("为了看看组员这边的安排是否还需要调整", output.message)
        self.assertIn("我补充一条新的试用结果", output.message)
        self.assertIn(unit.text.rstrip("。！？!?"), output.message)
        self.assertIn("原来的安排", output.message)
        self.assertIn("调整", output.message)
        self.assertEqual(agent._v11_contract_errors(output, payload), [])  # noqa: SLF001

        abrupt = output.model_copy(
            update={
                "message": (
                    f"好，组员这边先记下。{unit.text.rstrip('。！？!?')}；"
                    "这项变化会让你怎样修改原来的决定？"
                )
            }
        )
        self.assertIn(
            "missing_event_introduction",
            agent._v11_contract_errors(abrupt, payload),  # noqa: SLF001
        )

    def test_every_release_event_requires_a_reasoned_new_information_intro(
        self,
    ) -> None:
        blueprint = _blueprint()
        agent = InterviewerAgent()
        latest_by_event = {
            "evidence_uncertainty": "先确认大家的分工",
            "stakeholder_conflict": "最好召集大家开个会",
            "decision_pressure": "在进度允许时顾好质量",
            "counter_evidence": "先看组员这边",
            "integration": "再定最后的安排",
        }

        for event in blueprint.event_cards:
            if event.event_code not in latest_by_event:
                continue
            with self.subTest(event_code=event.event_code):
                unit = event.presentation_units[0]
                plan = _plan(
                    action="RELEASE_EVENT",
                    delivery_mode="event_link",
                    release_event_code=event.event_code,
                    release_unit_code=unit.unit_code,
                )
                context = _context(latest_by_event[event.event_code])
                output = agent._fallback(  # noqa: SLF001
                    plan,
                    blueprint,
                    context,
                    style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
                )
                payload = agent.runtime_renderer_input_payload(
                    context,
                    blueprint,
                    plan,
                    style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
                )

                self.assertTrue(
                    "为了" in output.message
                    or output.message.startswith(
                        ("为核实", "为比较", "为选择", "为调整", "为整合")
                    )
                )
                self.assertTrue(
                    "补充一条新" in output.message
                    or "补充一项新" in output.message
                    or "新信息：" in output.message
                    or "新限制：" in output.message
                    or "新结果：" in output.message
                )
                self.assertEqual(
                    agent._v11_contract_errors(output, payload),  # noqa: SLF001
                    [],
                )

                naked = output.model_copy(
                    update={
                        "message": (
                            f"{unit.text.rstrip('。！？!?')}；"
                            "这会让你怎样调整？"
                        )
                    }
                )
                self.assertIn(
                    "missing_event_introduction",
                    agent._v11_contract_errors(naked, payload),  # noqa: SLF001
                )

                out_of_order = output.model_copy(
                    update={
                        "message": (
                            f"我补充一条新信息："
                            f"{unit.text.rstrip('。！？!?')}；"
                            "为了继续判断，这会让你怎样调整？"
                        )
                    }
                )
                self.assertIn(
                    "event_introduction_out_of_order",
                    agent._v11_contract_errors(out_of_order, payload),  # noqa: SLF001
                )

    def test_meeting_before_stakeholder_event_gets_a_reasoned_transition(
        self,
    ) -> None:
        blueprint = _blueprint()
        event = next(
            item
            for item in blueprint.event_cards
            if item.event_code == "stakeholder_conflict"
        )
        unit = event.presentation_units[0]
        plan = _plan(
            action="RELEASE_EVENT",
            delivery_mode="event_link",
            release_event_code=event.event_code,
            release_unit_code=unit.unit_code,
        )
        context = _context("每个人具体干了什么，最好召集大家开个会")
        agent = InterviewerAgent()
        output = agent._fallback(  # noqa: SLF001
            plan,
            blueprint,
            context,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )

        self.assertIn("你想把大家叫到一起谈谈", output.message)
        self.assertIn("为了让会上的讨论更具体", output.message)
        self.assertIn("我补充一条新的参与者信息", output.message)
        self.assertIn(unit.text.rstrip("。！？!?"), output.message)
        self.assertEqual(output.message.count("？"), 1)

    def test_meeting_focus_gets_a_warm_grounded_followup(self) -> None:
        plan = _plan(
            target_dimension="multiple_perspectives",
            target_evidence="识别不同观点",
        )
        agent = InterviewerAgent()
        output = agent._fallback(  # noqa: SLF001
            plan,
            _blueprint(),
            _context("先召集大家开会吧"),
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )

        self.assertEqual(
            output.message,
            "你想把大家叫到一起谈谈。会上最想先听哪处不同想法？",
        )

    def test_evaluative_doing_is_rejected(self) -> None:
        agent = InterviewerAgent()
        context = _context("先召集大家开会吧")
        plan = _plan(target_dimension="problem_definition")
        payload = agent.runtime_renderer_input_payload(
            context,
            _blueprint(),
            plan,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )
        output = agent._fallback(  # noqa: SLF001
            plan,
            _blueprint(),
            context,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        ).model_copy(
            update={
                "message": "召集开会是个直接的做法；你希望大家先弄清哪个问题？"
            }
        )

        self.assertIn(
            "evaluative_acknowledgement",
            agent._v11_contract_errors(output, payload),  # noqa: SLF001
        )

    def test_adjacent_event_frames_rotate_for_all_runtime_styles(self) -> None:
        blueprint = _blueprint()
        event = next(
            item
            for item in blueprint.event_cards
            if item.event_code == "stakeholder_conflict"
        )
        unit = event.presentation_units[0]
        plan = _plan(
            action="RELEASE_EVENT",
            delivery_mode="event_link",
            release_event_code=event.event_code,
            release_unit_code=unit.unit_code,
        )
        agent = InterviewerAgent()
        previous_event = (
            "为了继续判断，我补充一条新的信息：旧事实；"
            "你会怎么处理？"
        )

        for style_version in (
            BASELINE_INTERVIEWER_STYLE,
            HUMANISTIC_INTERVIEWER_STYLE,
            HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        ):
            with self.subTest(style_version=style_version):
                first_context = _context("先看当前进度")
                first_payload = agent.runtime_renderer_input_payload(
                    first_context,
                    blueprint,
                    plan,
                    style_version=style_version,
                )
                first_output = agent._fallback(  # noqa: SLF001
                    plan,
                    blueprint,
                    first_context,
                    style_version=style_version,
                )
                second_context = _context(
                    "先看当前进度",
                    previous_ai=previous_event,
                )
                second_context.dialogue_history[0].content_type = "interview_event"
                second_payload = agent.runtime_renderer_input_payload(
                    second_context,
                    blueprint,
                    plan,
                    style_version=style_version,
                )
                second_output = agent._fallback(  # noqa: SLF001
                    plan,
                    blueprint,
                    second_context,
                    style_version=style_version,
                )

                self.assertEqual(
                    first_payload["selected_event_intro_frame"],
                    EVENT_INTRO_FRAME_SUPPLEMENT,
                )
                self.assertEqual(
                    second_payload["previous_event_intro_frame"],
                    EVENT_INTRO_FRAME_SUPPLEMENT,
                )
                self.assertEqual(
                    second_payload["selected_event_intro_frame"],
                    EVENT_INTRO_FRAME_EVIDENCE,
                )
                self.assertIn("我补充一条新", first_output.message)
                self.assertIn("这里先看一条新", second_output.message)
                for output in (first_output, second_output):
                    self.assertIn(unit.text.rstrip("。！？!?"), output.message)
                    self.assertEqual(output.message.count("？"), 1)
                    self.assertLessEqual(len(output.message), 90)

                replay_payload = agent.runtime_renderer_input_payload(
                    second_context,
                    blueprint,
                    plan,
                    style_version=style_version,
                )
                replay_output = agent._fallback(  # noqa: SLF001
                    plan,
                    blueprint,
                    second_context,
                    style_version=style_version,
                )
                self.assertEqual(replay_payload, second_payload)
                self.assertEqual(replay_output.message, second_output.message)

    def test_runtime_identity_and_selected_event_frame_are_enforced(self) -> None:
        renderer_input = {
            "validated_plan": {"action": "RELEASE_EVENT"},
            "selected_event_intro_frame": EVENT_INTRO_FRAME_EVIDENCE,
        }
        errors = InterviewerAgent.runtime_expression_errors(
            "我是AI访谈员。为了继续判断，我补充一条新的信息：事实；怎么处理？",
            renderer_input,
        )

        self.assertIn("legacy_interviewer_identity", errors)
        self.assertIn("event_intro_frame_mismatch", errors)

    def test_wrong_model_event_frame_is_repaired_then_falls_back_safely(
        self,
    ) -> None:
        blueprint = _blueprint()
        event = next(
            item
            for item in blueprint.event_cards
            if item.event_code == "stakeholder_conflict"
        )
        unit = event.presentation_units[0]
        plan = _plan(
            action="RELEASE_EVENT",
            delivery_mode="event_link",
            release_event_code=event.event_code,
            release_unit_code=unit.unit_code,
        )
        context = _context(
            "先看当前进度",
            previous_ai=(
                "为了继续判断，我补充一条新的信息：旧事实；"
                "你会怎么处理？"
            ),
        )
        context.dialogue_history[0].content_type = "interview_event"
        agent = InterviewerAgent()
        renderer_input = agent.runtime_renderer_input_payload(
            context,
            blueprint,
            plan,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )
        deterministic = agent._fallback(  # noqa: SLF001
            plan,
            blueprint,
            context,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )
        wrong_message = (
            f"为了比较，我补充一条新的信息："
            f"{unit.text.rstrip('。！？!?')}；"
            f"{renderer_input['selected_question']}"
        )
        correct_raw = json.dumps(
            {"message": deterministic.message},
            ensure_ascii=False,
        )
        wrong_raw = json.dumps({"message": wrong_message}, ensure_ascii=False)

        with (
            patch(
                "app.agents.runtime_interviewer_agent.get_settings",
                return_value=SimpleNamespace(MODEL_GATEWAY_MODE="real"),
            ),
            patch.object(
                agent,
                "_call",
                side_effect=[
                    (wrong_raw, "test-model"),
                    (correct_raw, "test-model"),
                ],
            ) as repaired_call,
        ):
            repaired = agent.render(
                context,
                blueprint,
                plan,
                previous_questions=[],
                style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
                timeout_seconds=10,
                primary_timeout_seconds=5,
                renderer_input=renderer_input,
            )

        self.assertEqual(repaired.status, "ok")
        self.assertEqual(repaired.model_attempt_count, 2)
        self.assertEqual(repaired.output.message, deterministic.message)
        self.assertIn(
            "这里先看一条新的信息",
            repaired_call.call_args_list[1].kwargs["repair"],
        )

        with (
            patch(
                "app.agents.runtime_interviewer_agent.get_settings",
                return_value=SimpleNamespace(MODEL_GATEWAY_MODE="real"),
            ),
            patch.object(
                agent,
                "_call",
                side_effect=[
                    (wrong_raw, "test-model"),
                    (wrong_raw, "test-model"),
                ],
            ),
        ):
            fallback = agent.render(
                context,
                blueprint,
                plan,
                previous_questions=[],
                style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
                timeout_seconds=10,
                primary_timeout_seconds=5,
                renderer_input=renderer_input,
            )

        self.assertEqual(fallback.status, "failed")
        self.assertTrue(fallback.output.fallback_used)
        self.assertIn("这里先看一条新", fallback.output.message)
        self.assertNotIn("我补充一条新", fallback.output.message)


if __name__ == "__main__":
    unittest.main()

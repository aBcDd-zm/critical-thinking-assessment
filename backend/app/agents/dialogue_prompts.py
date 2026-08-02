from __future__ import annotations

import json

from app.agents.question_contract import asked_questions, load_contract
from app.agents.schemas import AgentRuntimeContext


HOST_SYSTEM_PROMPT = """你是审辩式思维动态测评系统的主持 Agent。
请基于配置中的情境背景、阶段目标和主问题生成自然、专业、克制的阶段开场。
不要泄露评分维度、rubric 或标准答案。只输出 JSON。

字段取值必须严格使用以下枚举，不要翻译、不要自造：
- content_type: stage_question | advance_prompt | system_message
- generation_mode: config_guided | context_guided | ai_open
- next_action: wait_user_answer | advance_stage
"""

FOLLOWUP_SYSTEM_PROMPT = """你是审辩式思维动态测评系统的访谈 Agent。
请直接根据用户最新输入、完整阶段对话、候选追问策略、候选动态信息和阶段目标，
一次性生成下一条自然、简洁、面向用户的回复。
不要评分，不要生成报告，不要泄露评分标准。只输出 JSON。

回答类别、相关性、证据覆盖和动态信息触发均由你独立进行语义判断；系统不会提供本地关键词分类结果。
你必须返回 resolved_response_category：
- assess_answer：用户正在实质作答；
- clarify_question：用户没理解、希望重述或简化题目；
- explain_term：用户询问题目中的术语；
- encourage_answer：用户不知道、信息太少或只是寒暄；
- redirect：内容与当前问题无关。
请在 category_correction_reason 中简要说明本次分类依据。

人本主义访谈式追问规则：
1. 倾听接住：只承接用户已经明确表达过的内容，不替用户补充没说过的证据。
2. 复述澄清：用一句话概括用户当前判断或表达缺口，保持中性、非评判。
3. 安全化表达：保持中性、低压力，但“没有唯一标准答案”的理念只在整场开场说明一次，后续追问禁止重复这句话或近义模板。
4. 证据化追问：每轮只问一个核心问题，把问题落回理由、证据、权衡、假设、行动计划或调整依据。
5. 简单模式：language_mode=plain 时使用短句和日常用语，不使用未解释的专业词。
6. 单点互动：用户可见回复一般不超过两句话、一个问号；每轮只推进一个认知动作，如理由、证据、视角、边界或下一步，不把多个任务塞进同一轮。
7. 具体口语：优先使用短句、日常词和具体动作，避免直接使用“样本代表性、决策边界、关键假设、推理链、取舍依据”等未解释术语。
8. 个性化承接：承接用户回答时，要引用或转述用户刚刚表达的具体内容，避免反复使用“我想了解你的判断过程”等通用套话。
9. 降低负担：用户回答较短、犹豫或表示不知道时，先允许其从第一反应或一个具体点开始；可以提示回答方式，但不能提供答案内容。
10. 分轮追问：发现多个证据缺口时，只选择当前最重要的一项追问，其他缺口留到下一轮；禁止使用“哪些……以及……分别……”组合多个问题。
11. 年轻而专业：语气像一位平等、耐心的项目教练，不刻意装年轻，不使用网络热梗、emoji、亲昵称呼或夸张鼓励。
12. 中性反馈：不使用“正确、很好、充分、高分”等评价性表达，避免影响用户后续回答；可以说“我明白你的方向了”或直接自然承接。

边界：
- resolved_response_category=clarify_question 时，直接解释或重述用户没理解的部分；即使此前已经解释两次，也继续回答。
- resolved_response_category=explain_term 时，用日常语言解释术语，再自然地把对话接回本题。
- resolved_response_category=encourage_answer 时，降低作答压力并给一个容易开始的开放提示，不提供标准答案。
- resolved_response_category=redirect 时，中性承接后把对话拉回当前问题，不使用责备式固定文案。
- 上述四类不得选择或释放动态信息，target_dimensions 必须为空，next_action 必须为 ask_followup。
- resolved_response_category=assess_answer 时，阶段主问题已经在开场展示过，不要机械重复原文；
  应针对用户最新回答中的具体信息追问，或补充更深入的视角。回答充分时可推进阶段。
- 必须综合当前阶段的全部用户回答，对每一项 expected_evidence 返回 resolved_evidence：
  - coverage=covered：已有明确语义证据，必须列出 supporting_turn_indexes；
  - coverage=partial：已经提到但不足以完成该项，可列出支撑消息；
  - coverage=missing：当前阶段尚未表达，不得虚构支撑消息。
- evidence_key 只能使用阶段完成证据中的原始名称；supporting_turn_indexes 只能引用当前阶段用户消息。
- 按阶段完成证据中的 evidence_guidance 判断自然语言语义，不要求用户使用专业术语或固定句式。
- 用户描述分工、协调、沟通、排查、验证、决策或执行等管理动作时，必须结合阶段问题理解其隐含对象和目的，
  不得仅因没有复述题干词语就判为 redirect；只有与当前情境和问题确实无关时才使用 redirect。
- 用户明确指出“现有信息不足、不能据此下结论、还需要核实”时，这是有效的证据边界意识；
  不得把它一律当成低信息回答。
- 测评的职责是记录用户自然表现并补足可观察证据，不是通过连续提示把用户训练成高分答案。
- 各阶段的证据判定语义（如原因诊断的接受方式、按项数计覆盖、可评分但非必答的表现）
  以消息中的 evidence_guidance 和“阶段追问约束”为准，不要另行发明规则。
- 不得要求用户声称或猜测题干没有提供的事实。若实际用户类型、设备、网络或数据未知，
  应询问用户“会核实哪些信息、如何核实、核实后怎样调整判断”，而不是问“实际来自谁/实际是什么”。
- 只有动态信息触发条件与用户回答的实际语义匹配时，才选择对应 code；不匹配时返回 null。
- 服务端会基于你返回的分类与证据覆盖执行追问上限和阶段状态机；不得虚构状态。
- 每次追问必须绑定当前情境、阶段目标、候选策略和目标维度。
- 不要暗示高分路径，不要说“正确做法是/你应该/标准答案”。
- 不要把挑战写成审问或否定，优先使用“我想了解/可以具体说说/你会如何判断”。
- 承接语要结合用户刚才的具体回答，避免每轮重复同一句“我更想了解你的判断过程”。
- 如果回答很短，先降低压力，再请用户给出一个具体判断或依据。
- 只有用户已经给出实质性判断且动态信息的触发条件与该判断语义匹配时，才可选择动态信息；不得因为回答较短而释放新信息。

字段取值必须严格使用以下枚举，不要翻译、不要自造：
- resolved_response_category: assess_answer | clarify_question | explain_term | encourage_answer | redirect
- content_type: followup_question | dynamic_info_question | clarification_response | guidance_response | term_explanation | redirect_response | stage_incomplete_prompt | supplement_question | advance_prompt | system_message
- question_type: clarify | open_followup | challenge | trap | dynamic_update | advance
- generation_mode: fixed_question | template_guided | strategy_guided | ai_open
- next_action: ask_followup | advance_stage | finish_ready
- transition_reason: evidence_complete | followup_limit_reached | user_navigation | null

输出 JSON 字段：
- question: 最终给用户看的回复；最多两句话、一个问号，先简短承接，再只问一个核心问题。
- resolved_response_category: 结合语义校正后的最终分类。
- category_correction_reason: 可选；分类发生修正时说明原因，否则为 null。
- resolved_evidence: 数组；每项包含 evidence_key, coverage, supporting_turn_indexes, reason, confidence。
- humanistic_steps: 对象，包含 listening_acknowledgement, reflective_clarification, safety_prompt, evidence_probe；用于审计，不要求完整展示给用户。
- reflection_summary: 对用户最新回答的中性复述。
- evidence_gap: 当前最需要补充的可评分证据缺口。
- target_dimensions: 本轮追问服务的维度 key 数组。
- trigger_reason: 为什么需要这轮追问。
- 以及 content_type, question_type, selected_rule_code, selected_dynamic_info_code, released_dynamic_info_text, generation_mode, ai_generation_weight, reason, next_action, transition_reason, confidence。
"""


def build_host_messages(context: AgentRuntimeContext) -> list[dict[str, str]]:
    stage = context.stage
    nickname = context.participant.nickname or "受测者"
    content = "\n".join(
        [
            f"用户昵称：{nickname}",
            f"情境标题：{context.scenario.title}",
            f"情境背景：{context.scenario.background}",
            f"阶段编码：{stage.stage_code}",
            f"阶段名称：{stage.title}",
            f"阶段目标：{stage.stage_goal}",
            f"阶段上下文：{stage.context}",
            f"阶段主问题：{stage.main_question}",
            f"context_generation_mode：{stage.context_generation_mode}",
            f"context_ai_weight：{stage.context_ai_weight}",
            "输出 JSON 字段：stage_code, message, content_type, generation_mode, ai_generation_weight, reason, next_action。",
        ]
    )
    return [
        {"role": "system", "content": HOST_SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


_CONSTRAINT_RULE_LINES = {
    "single_question_mark": "每轮回复最多一个问号，不把多个问题合并在同一轮。",
    "no_compound_request": "禁止一次索要两项或多项内容（如“哪两项”“分别是什么”）；一轮只收集一项。",
    "no_reask_core": (
        "用户已经提出或再次确认核心判断后，不得换一种说法重问“核心问题是什么”；"
        "应转向尚未表达的限制条件、范围或边界。"
    ),
    "no_cross_stage_duplicate": "不得重复或换述本次测评中已经问过的任何正式问题（见已问问题清单）。",
}


def render_contract_rules(stage) -> list[str]:
    """Natural-language rule lines derived from the stage's question contract."""
    contract = load_contract(stage)
    lines: list[str] = []
    probes = [probe for probe in (contract.get("probes") or []) if isinstance(probe, dict)]
    if probes:
        if any(probe.get("mode") == "strategy_guided" for probe in probes):
            lines.append(
                "定点追问：当核心判断已有证据且限制条件未集齐时，先用一句承接用户刚才的回答，"
                "再只追问一项现实限制；已有一项时，明确要求一项不同的限制。"
                "措辞不符合约束时系统会改用固定问句。"
            )
        else:
            lines.append(
                "本阶段的定点追问由系统按证据覆盖状态逐项收集（每轮只补一项）；"
                "你仍需按 evidence_guidance 判定各项覆盖，不要一次索要多项。"
            )
    for name in contract.get("constraints") or []:
        rule = _CONSTRAINT_RULE_LINES.get(str(name))
        if rule:
            lines.append(rule)
    return lines


def build_followup_messages(context: AgentRuntimeContext) -> list[dict[str, str]]:
    stage = context.stage
    latest = context.latest_user_turn.content if context.latest_user_turn else "无"
    history = "\n".join(
        f"- [{turn.speaker}/{turn.content_type}] {turn.content}"
        for turn in context.dialogue_history[-8:]
    )
    stage_user_answers = "\n".join(
        (
            f"- turn_index={turn.turn_index}, content={turn.content}, "
            f"analysis={_json_dump(_evidence_analysis_summary(turn.analysis_json))}"
        )
        for turn in context.dialogue_history
        if turn.speaker == "user" and turn.stage_code == stage.stage_code
    )
    rules = "\n".join(
        (
            f"- code={rule.rule_code}, type={rule.rule_type}, priority={rule.priority}, "
            f"trigger={rule.trigger_condition or ''}, strategy={rule.strategy_direction}, "
            f"sample={rule.sample_question or ''}, fallback={rule.fallback_question or ''}, "
            f"mode={rule.question_generation_mode}, ai_weight={rule.question_ai_weight}, "
            f"constraints={_json_dump(rule.question_generation_constraints_json)}, "
            f"target_dimensions={rule.target_dimensions}"
        )
        for rule in context.candidate_intervention_rules
    )
    infos = "\n".join(
        (
            f"- code={info.info_code}, type={info.info_type}, priority={info.priority}, "
            f"title={info.title}, content={info.content}, trigger={info.trigger_condition or ''}, "
            f"target_dimensions={info.target_dimensions}"
        )
        for info in context.candidate_dynamic_infos
    )
    contract_rules = render_contract_rules(stage)
    previous_questions = asked_questions(context)
    exit_criteria_view = {
        key: value
        for key, value in (stage.exit_criteria or {}).items()
        if key != "question_contract"
    }
    content = "\n".join(
        [
            f"当前阶段：{stage.title}（{stage.stage_code}）",
            f"阶段目标：{stage.stage_goal}",
            f"阶段主问题：{stage.main_question}",
            f"阶段追问上限：{stage.max_followups}",
            f"阶段完成证据：{_json_dump(exit_criteria_view)}",
            "阶段追问约束：",
            "\n".join(f"- {line}" for line in contract_rules) or "无",
            "本次测评已提出过的正式问题（不得重复或换述重复）：",
            "\n".join(f"- {item[:60]}" for item in previous_questions[-20:]) or "无",
            f"表达模式：{context.session.language_mode}",
            f"用户最新回答：{latest}",
            "当前阶段全部用户回答（语义证据必须综合这些消息）：",
            stage_user_answers or "无",
            "近期对话：",
            history or "无",
            "候选追问策略：",
            rules or "无",
            "候选动态信息：",
            infos or "无",
            "输出 JSON 字段：question, content_type, question_type, resolved_response_category, category_correction_reason, resolved_evidence, selected_rule_code, selected_dynamic_info_code, released_dynamic_info_text, target_dimensions, trigger_reason, reflection_summary, evidence_gap, humanistic_steps, generation_mode, ai_generation_weight, reason, next_action, transition_reason, confidence。",
        ]
    )
    return [
        {"role": "system", "content": FOLLOWUP_SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


def _json_dump(value: object) -> str:
    if value is None:
        return "null"
    return json.dumps(value, ensure_ascii=False)


def _evidence_analysis_summary(value: dict | None) -> dict | None:
    if not value:
        return None
    return {
        key: value.get(key)
        for key in (
            "resolved_response_category",
            "resolved_evidence",
        )
        if value.get(key) is not None
    }


__all__ = ["build_followup_messages", "build_host_messages"]

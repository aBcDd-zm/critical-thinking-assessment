from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from app.agents.progressive_schemas import InterviewPlanOutput


INTENT_REGISTRY_VERSION = "humanistic_v1_1_intent_registry_v4"


@dataclass(frozen=True)
class IntentFamilySpec:
    family_id: str
    candidates: tuple[str, str, str]
    semantic_groups: tuple[tuple[str, ...], ...]
    compact_fallback: str


@dataclass(frozen=True)
class IntentBinding:
    family: IntentFamilySpec
    mapping_source: str
    mapping_fields: tuple[str, ...]
    fingerprint: str


def _family(
    family_id: str,
    candidates: tuple[str, str, str],
    semantic_groups: tuple[tuple[str, ...], ...],
    compact_fallback: str,
) -> IntentFamilySpec:
    return IntentFamilySpec(
        family_id=family_id,
        candidates=candidates,
        semantic_groups=semantic_groups,
        compact_fallback=compact_fallback,
    )


INTENT_FAMILIES: dict[str, IntentFamilySpec] = {
    "problem_scope": _family(
        "problem_scope",
        (
            "你认为当前最需要解决的问题是什么，它的边界或限制在哪里？",
            "在采取行动前，你会怎样界定核心问题和约束？",
            "这项决定首先要解决什么，哪些范围或条件不能忽略？",
        ),
        (("问题", "矛盾", "目标", "决定"), ("边界", "约束", "限制", "范围", "条件")),
        "核心问题和边界是什么？",
    ),
    "evidence_decision_compare": _family(
        "evidence_decision_compare",
        (
            "支持改变当前决定前，你还需要比较哪些证据？",
            "哪些新增证据能让你判断要不要改变当前选择？",
            "要决定是否改变，你还会核对哪几项证据？",
        ),
        (("改变", "选择", "决定"), ("证据", "核对")),
        "改变决定前还要核对什么？",
    ),
    "evidence_coverage_gap": _family(
        "evidence_coverage_gap",
        (
            "未覆盖的部分会怎样影响现有判断，你还需补查什么？",
            "现有证据没有覆盖的范围可能改变什么结论？",
            "要判断未覆盖部分的影响，你会再验证什么？",
        ),
        (("未覆盖", "没有覆盖"), ("影响", "改变", "验证", "补查")),
        "未覆盖部分还要验证什么？",
    ),
    "evidence_validation_method": _family(
        "evidence_validation_method",
        (
            "你会用哪项具体证据来验证这项能力？",
            "用什么可观察结果能检验这项判断？",
            "你会怎样收集并核对支持这项能力的证据？",
        ),
        (("证据", "结果"), ("验证", "检验", "核对")),
        "你会用什么证据验证？",
    ),
    "evidence_sample_representativeness": _family(
        "evidence_sample_representativeness",
        (
            "现有样本能否代表整批，你会补充什么验证？",
            "你会怎样检验样本代表性并补查整批情况？",
            "要把样本结论用于整批，还需要哪项补充证据？",
        ),
        (("样本",), ("代表", "整批"), ("验证", "证据", "补查")),
        "样本代表性还要怎样验证？",
    ),
    "evidence_independent_verification": _family(
        "evidence_independent_verification",
        (
            "除厂商演示外，你会用哪项独立数据核实结论？",
            "你会怎样独立验证厂商展示的结果？",
            "哪种不由厂商提供的数据能检验这项结论？",
        ),
        (("独立", "不由厂商"), ("数据", "结果"), ("核实", "验证", "检验")),
        "你会用什么独立数据核实结论？",
    ),
    "evidence_transfer_validity": _family(
        "evidence_transfer_validity",
        (
            "你还要核实什么，才能判断实验结果适用于实际运输？",
            "从实验环境到实际运输，还需要哪项验证？",
            "哪些实际运输数据能检验实验结果能否适用？",
        ),
        (("实验",), ("实际运输",), ("核实", "验证", "检验")),
        "实验结果用于实际运输前还要验证什么？",
    ),
    "reasoning_consequence_compare": _family(
        "reasoning_consequence_compare",
        (
            "调人与不调人分别可能带来什么主要后果？",
            "你会怎样比较调人与维持现状的影响？",
            "两个选择各自的收益和风险是什么？",
        ),
        (("调人", "两个选择"), ("后果", "影响", "收益", "风险")),
        "两个选择各有什么后果？",
    ),
    "reasoning_causal_comparison": _family(
        "reasoning_causal_comparison",
        (
            "还需要什么比较证据，才能区分同时发生与因果关系？",
            "你会怎样比较，才能判断两者只是相关还是存在因果？",
            "哪些对照信息能检验这两件事之间的因果关系？",
        ),
        (("比较", "对照"), ("因果",), ("证据", "信息", "检验", "判断")),
        "还要比较什么证据来判断因果？",
    ),
    "reasoning_condition_link": _family(
        "reasoning_condition_link",
        (
            "成本回收怎样支持这个结论，它还需要哪些成立条件？",
            "从成本回收到值得实施，中间的判断依据是什么？",
            "这个结论在什么条件下成立，又在什么情况下不成立？",
        ),
        (("成本", "结论"), ("条件", "依据", "情况下")),
        "这个结论在什么条件下成立？",
    ),
    "decision_reversible_options": _family(
        "decision_reversible_options",
        (
            "除了直接增加备货，还有什么更可逆的安排？",
            "你会怎样比较增加备货和可随时调整的方案？",
            "哪种替代方案既能试行，又能控制撤回成本？",
        ),
        (("可逆", "随时调整", "撤回"), ("方案", "安排")),
        "还有什么可逆方案？",
    ),
    "decision_continue_stop_rules": _family(
        "decision_continue_stop_rules",
        (
            "继续试投放需要达到什么标准，出现什么结果时停止？",
            "你会用哪项结果作为继续或停止试投放的条件？",
            "再观察一天后，达到什么条件继续，什么条件停止？",
        ),
        (("继续",), ("停止",), ("标准", "结果", "条件")),
        "继续或停止的条件是什么？",
    ),
    "decision_initial_choice": _family(
        "decision_initial_choice",
        (
            "在已有安排、减少检查和小范围试用中，你目前会选择哪一种？",
            "在新信息出现前，你现在的初步决定是什么？",
            "面对现有几个选项，你目前更倾向采用哪一种安排？",
        ),
        (("选择", "决定", "倾向"), ("安排", "选项", "试用")),
        "你现在会选择哪项安排？",
    ),
    "perspective_priority_impact": _family(
        "perspective_priority_impact",
        (
            "只按提交时间排序，能否反映不同需求被延迟后的业务影响？",
            "除提交时间外，你还会比较哪些延迟影响来确定优先级？",
            "要检验当前排序，你会怎样比较两个部门受影响的程度？",
        ),
        (("提交时间", "当前排序"), ("影响",), ("优先级", "排序", "比较")),
        "当前排序还要比较哪些影响？",
    ),
    "perspective_impact_compare": _family(
        "perspective_impact_compare",
        (
            "排班决定中，哪些人的不同影响需要一起比较？",
            "你会怎样比较这项排班对不同参与者的影响？",
            "不同相关方各会承担什么影响，你会如何权衡？",
        ),
        (("不同",), ("影响",), ("比较", "权衡")),
        "哪些不同影响需要一起比较？",
    ),
    "perspective_tradeoff": _family(
        "perspective_tradeoff",
        (
            "进度和风险需要兼顾时，你会依据什么作取舍？",
            "你会怎样兼顾按时完成和避免返工？",
            "这两方面发生冲突时，你准备怎样权衡？",
        ),
        (
            ("兼顾", "权衡", "取舍"),
            ("进度", "按时", "两方面"),
            ("风险", "返工", "冲突"),
        ),
        "进度和风险冲突时怎样权衡？",
    ),
    "perspective_coordination": _family(
        "perspective_coordination",
        (
            "面对不同参与者的关注，你会依据什么协调？",
            "进度和质量诉求不同，你会怎样协调双方？",
            "双方关注不一致时，你会用什么依据安排先后？",
        ),
        (("协调", "安排"), ("不同", "双方"), ("依据", "先后", "怎样")),
        "双方关注不同时依据什么协调？",
    ),
    "adjustment_rollback_condition": _family(
        "adjustment_rollback_condition",
        (
            "小范围试用出现什么情况时，你会回退到原安排？",
            "你会用什么条件决定继续试用还是回退？",
            "看到什么结果时，你会停止试用并恢复原安排？",
        ),
        (("试用",), ("回退", "恢复"), ("条件", "情况", "结果")),
        "试用出现什么结果时回退？",
    ),
    "evidence_information_threshold": _family(
        "evidence_information_threshold",
        (
            "你还需要看到什么信息，才能形成判断？",
            "哪些信息得到核实后，你才会愿意作判断？",
            "目前缺少哪项信息，使你暂时不下结论？",
        ),
        (("信息",), ("判断", "结论"), ("看到", "核实", "缺少")),
        "还缺少什么信息才能作判断？",
    ),
    "event_evidence_reassessment": _family(
        "event_evidence_reassessment",
        (
            "这条新信息会怎样改变你对现有证据的判断？",
            "知道这条情况后，你还要补查什么才能重新判断？",
            "现有证据是否仍然够用，你会用什么补充核实？",
        ),
        (("新信息", "这条情况", "现有证据"), ("判断", "补查", "核实")),
        "现有证据还要补查什么？",
    ),
    "event_priority_adjustment": _family(
        "event_priority_adjustment",
        (
            "这条新信息会让你怎样调整原来的优先顺序？",
            "重新安排优先级时，你会依据什么条件？",
            "原有先后顺序中，哪一项需要因这条情况而改变？",
        ),
        (("优先", "先后顺序"), ("调整", "重新安排", "改变")),
        "你会怎样调整优先级？",
    ),
    "event_scope_adjustment": _family(
        "event_scope_adjustment",
        (
            "这条新信息会让你怎样调整试点范围或步骤？",
            "原试点安排中，哪些部分保留，哪些部分缩小？",
            "面对这项变化，你会怎样改动试点安排？",
        ),
        (("试点",), ("调整", "改动", "缩小", "保留")),
        "试点安排要怎样调整？",
    ),
    "event_contingency": _family(
        "event_contingency",
        (
            "这条新信息出现后，你会采用什么替代安排？",
            "原安排受限时，你准备怎样设置备选方案？",
            "哪项替代步骤能在当前限制下继续推进？",
        ),
        (("替代", "备选"), ("安排", "方案", "步骤")),
        "你会采用什么备选安排？",
    ),
    "event_process_adjustment": _family(
        "event_process_adjustment",
        (
            "新要求出现后，你会调整流程中的哪一步？",
            "原流程要怎样改动，才能纳入这项新要求？",
            "你会保留哪些步骤，又增加哪项流程安排？",
        ),
        (("流程", "步骤"), ("调整", "改动", "增加", "纳入")),
        "流程中的哪一步要调整？",
    ),
    "event_adjustment_criteria": _family(
        "event_adjustment_criteria",
        (
            "面对这条新信息，你会依据什么调整原决定？",
            "哪些条件会决定你保留还是改变当前安排？",
            "调整原方案时，你最先比较哪项依据？",
        ),
        (("依据", "条件"), ("调整", "改变", "保留")),
        "你会依据什么调整？",
    ),
    "event_risk_control": _family(
        "event_risk_control",
        (
            "这条新信息会让你怎样调整风险控制安排？",
            "现有控制措施中，哪一项需要立即改变？",
            "你会增加什么可执行措施来控制这项新风险？",
        ),
        (("风险", "控制措施"), ("调整", "改变", "增加")),
        "风险控制要怎样调整？",
    ),
    "event_plan_revision": _family(
        "event_plan_revision",
        (
            "这条新信息会改变原安排的哪一部分，为什么？",
            "原计划中哪些部分保留，哪些部分需要调整？",
            "面对这项变化，你会怎样修改下一步安排？",
        ),
        (("安排", "计划"), ("改变", "保留", "调整", "修改")),
        "原安排要调整什么？",
    ),
    "event_uncertainty_check": _family(
        "event_uncertainty_check",
        (
            "看到这项延迟记录后，你准备先核实什么？",
            "这几次延迟里，你会先查哪类记录？",
            "要弄清延迟原因，你准备先确认哪个环节？",
        ),
        (("延迟", "记录"), ("核实", "查", "确认")),
        "这几次延迟先查什么？",
    ),
    "event_stakeholder_priority": _family(
        "event_stakeholder_priority",
        (
            "进度和质量发生冲突时，你会先比较什么？",
            "一边想赶进度、一边担心返工，你会依据什么安排先后？",
            "两边关注不同，你会先比较哪方面的影响？",
        ),
        (("进度", "质量", "返工", "两边"), ("比较", "先后")),
        "进度和质量冲突时先比较什么？",
    ),
    "event_decision_under_constraint": _family(
        "event_decision_under_constraint",
        (
            "在当前约束下，你会先形成什么安排？",
            "面对这项限制，你会依据什么作出初步决定？",
            "现有条件受限时，你准备先采取哪项行动？",
        ),
        (("约束", "限制", "受限"), ("安排", "决定", "行动")),
        "当前约束下你会怎样安排？",
    ),
    "event_judgment_revision": _family(
        "event_judgment_revision",
        (
            "你会依据这项变化怎样调整原来的安排？",
            "根据这个新结果，你准备保留还是改变原来的安排？",
            "这项变化会让你怎样修改原来的决定？",
        ),
        (("变化", "新结果"), ("调整", "改变", "修改", "保留"), ("原来的安排", "原来的决定")),
        "这项变化会让你怎样调整原来的安排？",
    ),
    "event_final_plan": _family(
        "event_final_plan",
        (
            "结合现有信息，你的最终方案和调整条件是什么？",
            "你会怎样形成可执行安排，并设置后续调整条件？",
            "最终先做什么，看到什么结果时再调整？",
        ),
        (("最终", "可执行", "先做"), ("调整",)),
        "最终方案和调整条件是什么？",
    ),
    "clarify_observable_metric": _family(
        "clarify_observable_metric",
        (
            "你说的“效果”，具体可以用什么可观察结果表示？",
            "哪些实际指标出现时，你会认为达到了“效果”？",
            "把“效果”具体化后，你最先会看哪项指标？",
        ),
        (("效果",), ("观察", "结果", "指标")),
        "“效果”具体看什么指标？",
    ),
    "clarify_decision_threshold": _family(
        "clarify_decision_threshold",
        (
            "你说的“风险太大”，具体达到什么程度就不推进？",
            "哪项可观察结果会触发你停止推进？",
            "你会用什么阈值区分可接受与不可接受的风险？",
        ),
        (("风险", "结果"), ("程度", "阈值", "触发")),
        "风险到什么程度会触发停止？",
    ),
    "clarify_sample_basis": _family(
        "clarify_sample_basis",
        (
            "你说“普遍”，具体来自哪些客户样本或记录？",
            "目前有哪些样本和记录支持“普遍”这个判断？",
            "“普遍”覆盖了哪些客户样本，又依据了什么记录？",
        ),
        (("普遍",), ("样本",), ("记录",)),
        "“普遍”依据哪些样本记录？",
    ),
    "clarify_scope_basis": _family(
        "clarify_scope_basis",
        (
            "你说的“最重要”依据什么判断，具体包括哪些范围？",
            "哪些条件让这部分成为“最重要”，范围到哪里？",
            "“最重要”具体指哪部分，你用什么依据区分？",
        ),
        (("最重要",), ("依据", "条件"), ("范围", "哪部分")),
        "“最重要”的范围和依据是什么？",
    ),
    "clarify_agreement_basis": _family(
        "clarify_agreement_basis",
        (
            "你说团队基本同意，具体是哪些成员表达了什么意见？",
            "哪些成员明确同意，哪些成员还保留意见？",
            "“基本同意”分别体现在哪些人的什么表态上？",
        ),
        (("成员", "哪些人"), ("同意", "意见", "表态")),
        "哪些成员表达了同意？",
    ),
    "clarify_restate": _family(
        "clarify_restate",
        (
            "简单说，你准备先核实哪一点？",
            "换成更直接的问法：你会先判断什么？",
            "具体一点，你现在最想确认什么？",
        ),
        (("先", "现在"), ("弄清", "判断", "确认", "核实")),
        "我换个具体问法：你想先确认什么？",
    ),
    "clarify_low_information": _family(
        "clarify_low_information",
        (
            "先说一个具体的人或任务就可以，你想到哪一个？",
            "不用一次说完整，你想先从哪部分开始？",
            "我们把范围缩小一点：你最想先看谁或哪项任务？",
        ),
        (("哪", "谁", "什么"),),
        "你想先看谁或哪项任务？",
    ),
    "clarify_plain_language": _family(
        "clarify_plain_language",
        (
            "可以先按日常理解来回答，你现在最直接的判断是什么？",
            "不需要使用专业术语，你会怎样理解眼前的情况？",
            "可以只谈眼前任务，你首先会怎样判断？",
        ),
        (("理解", "判断"),),
        "你最直接的判断是什么？",
    ),
    "repair_evidence_criterion": _family(
        "repair_evidence_criterion",
        (
            "刚才的问题重复了；核对退款记录后，你会用什么标准判断？",
            "我换一个未问过的角度：退款记录出现什么结果会改变判断？",
            "不再重复前一问；你会怎样用退款记录形成判断标准？",
        ),
        (("退款记录",), ("标准", "结果"), ("判断",)),
        "核对退款记录后用什么标准判断？",
    ),
    "repair_pilot_validation": _family(
        "repair_pilot_validation",
        (
            "刚才理解反了；缩小试点后，你最需要验证什么？",
            "我按缩小范围重新问：这次试点要检验哪项目标？",
            "承接你的纠正；缩小试点要得到什么结果才有意义？",
        ),
        (("缩小",), ("试点",), ("验证", "检验", "结果")),
        "缩小试点后要验证什么？",
    ),
    "repair_cross_validation": _family(
        "repair_cross_validation",
        (
            "刚才偏离了焦点；你会怎样交叉核对信息来源？",
            "我回到信息可靠性：哪些来源需要相互验证？",
            "换回你关心的角度：你会用什么来源交叉验证？",
        ),
        (("来源",), ("交叉", "相互"), ("核对", "验证")),
        "你会怎样交叉核对来源？",
    ),
    "repair_dimension_problem": _family(
        "repair_dimension_problem",
        (
            "刚才没有承接好，我们换个角度：你最需要界定什么问题和边界？",
            "刚才没有承接好，不重复前一问：当前核心问题的范围是什么？",
            "刚才没有承接好，我换一个角度：这项决定受哪些边界限制？",
        ),
        (("问题", "决定"), ("边界", "范围", "限制")),
        "当前问题的边界是什么？",
    ),
    "repair_dimension_evidence": _family(
        "repair_dimension_evidence",
        (
            "刚才没有承接好，我们换个角度：你还需要核实哪类信息？",
            "刚才没有承接好，不重复前一问：你会用什么新证据检查判断？",
            "刚才没有承接好，我换一个角度：哪项来源还需要验证？",
        ),
        (("核实", "证据", "来源"), ("信息", "判断", "验证")),
        "还要核实什么信息来形成判断？",
    ),
    "repair_dimension_reasoning": _family(
        "repair_dimension_reasoning",
        (
            "刚才没有承接好，我们换个角度：哪项依据最支持你的结论？",
            "刚才没有承接好，不重复前一问：依据与结论是什么关系？",
            "刚才没有承接好，我换一个角度：什么条件会让理由不成立？",
        ),
        (("依据", "理由", "条件"), ("结论", "关系", "成立")),
        "哪项依据最支持结论？",
    ),
    "repair_dimension_perspective": _family(
        "repair_dimension_perspective",
        (
            "刚才没有承接好，我们换个角度：其他参与者会受到什么影响？",
            "刚才没有承接好，不重复前一问：不同相关方有哪些诉求？",
            "刚才没有承接好，我换一个角度：另一方最在意什么风险？",
        ),
        (("参与者", "相关方", "另一方"), ("影响", "诉求", "风险")),
        "其他参与者会受什么影响？",
    ),
    "repair_dimension_decision": _family(
        "repair_dimension_decision",
        (
            "刚才没有承接好，我们换个角度：你会先保留哪一步、调整哪一步？",
            "刚才没有承接好，不重复前一问：怎样把判断转成具体安排？",
            "刚才没有承接好，我换一个角度：下一步由谁先做什么？",
        ),
        (("保留", "安排", "谁"), ("调整", "具体", "先做")),
        "你会保留和调整哪一步？",
    ),
    "repair_dimension_adjustment": _family(
        "repair_dimension_adjustment",
        (
            "刚才没有承接好，我们换个角度：什么新信息会让你调整安排？",
            "刚才没有承接好，不重复前一问：什么条件会改变当前计划？",
            "刚才没有承接好，我换一个角度：哪些部分会随结果变化？",
        ),
        (("新信息", "条件", "结果"), ("调整", "改变", "变化")),
        "什么新信息会让你调整？",
    ),
    "redirect_decision_criteria": _family(
        "redirect_decision_criteria",
        (
            "我不会替你选择；你比较两个选项时最看重什么依据？",
            "决定仍由你作出；哪个判断条件最影响你的选择？",
            "我不提供替代决定；你会依据什么标准比较选项？",
        ),
        (("不会替", "由你", "不提供替代"), ("依据", "条件", "标准")),
        "我不会替你选择；你会依据什么条件？",
    ),
    "redirect_observable_tradeoff": _family(
        "redirect_observable_tradeoff",
        (
            "我不作临床分析或替代决定；你会比较哪些可观察的岗位条件和取舍？",
            "我们只看岗位事实；哪些条件和取舍最影响你的选择？",
            "我不会推断动机或替你选；你会依据哪些岗位条件判断？",
        ),
        (("不作临床", "岗位事实", "不会推断"), ("条件",), ("取舍", "选择", "判断")),
        "我不会推断个人情况；你会比较哪些岗位条件来取舍？",
    ),
    "pure_authority_criteria": _family(
        "pure_authority_criteria",
        (
            "对你来说，作出这个决定的首要判断标准是什么？",
            "你会先看哪一个条件来作出自己的决定？",
            "哪项信息会最影响你自己的选择？",
        ),
        (("判断标准", "条件", "信息"), ("决定", "选择")),
        "你会依据什么条件自己选择？",
    ),
    "integration_monitor_pause": _family(
        "integration_monitor_pause",
        (
            "试点范围、监测指标和暂停条件怎样组成最终方案？",
            "你会怎样确认试点安排，并明确监测和暂停条件？",
            "最终方案中，试点怎么做、看什么指标、何时暂停？",
        ),
        (("试点",), ("监测", "指标"), ("暂停",)),
        "试点的监测和暂停条件是什么？",
    ),
    "integration_adjust_review": _family(
        "integration_adjust_review",
        (
            "临时调配、两项监测指标和复盘时间怎样组成完整安排？",
            "你会怎样落实临时调配，并设置指标和复盘时间？",
            "最终由谁临时调整、看哪些指标、何时复盘？",
        ),
        (("临时",), ("指标",), ("复盘",)),
        "临时安排用什么指标，何时复盘？",
    ),
    "integration_launch_gate": _family(
        "integration_launch_gate",
        (
            "试用范围、上线前条件和推广证据怎样组成最终安排？",
            "你会怎样限定试用范围，并设置上线和推广条件？",
            "最终先在哪试用，满足什么条件后上线和推广？",
        ),
        (("试用",), ("上线",), ("推广",)),
        "试用达到什么条件才上线推广？",
    ),
    "integration_staged_exit": _family(
        "integration_staged_exit",
        (
            "分批下单和取消条件怎样共同控制风险？",
            "你会怎样安排分批执行，并明确何时取消？",
            "最终分几步下单，出现什么结果时停止或取消？",
        ),
        (("分批", "分几步"), ("取消", "停止")),
        "分批和取消条件是什么？",
    ),
    "integration_general": _family(
        "integration_general",
        (
            "把已谈到的依据、风险和行动放在一起，你会怎样安排？",
            "综合现有信息，你的具体方案和取舍是什么？",
            "如果现在开始执行，你会先做什么，并怎样控制风险？",
        ),
        (("依据", "信息", "执行"), ("安排", "方案", "先做"), ("风险", "取舍")),
        "综合现有信息和风险，你会先做什么安排？",
    ),
    "dimension_problem": _family(
        "dimension_problem",
        (
            "你觉得眼下最需要先判断的具体问题是什么？",
            "在开始行动前，你最需要先弄清哪一个问题？",
            "这个问题最关键的边界或限制是什么？",
        ),
        (("问题",),),
        "最需要判断的问题是什么？",
    ),
    "problem_cause_scope": _family(
        "problem_cause_scope",
        (
            "要弄清延迟出在哪个环节，你会先查什么？",
            "判断延迟是谁造成的，你准备先看哪类记录？",
            "你会先核对谁的任务和交接，来定位延迟原因？",
        ),
        (("延迟", "原因"), ("查", "看", "核对")),
        "延迟原因先查什么？",
    ),
    "dimension_evidence": _family(
        "dimension_evidence",
        (
            "你准备先核实哪类信息，再作判断？",
            "现有说法里，哪一点还需要查证后才能相信？",
            "还缺少什么信息，才能排除另一种可能？",
        ),
        (("信息", "说法"), ("核实", "查证", "缺少")),
        "你会先核实什么信息？",
    ),
    "dimension_reasoning": _family(
        "dimension_reasoning",
        (
            "你这个判断最主要的依据是什么？",
            "哪些事实能支持这个结论，它们之间是什么关系？",
            "什么情况出现时，你现在的理由会不再成立？",
        ),
        (("依据", "事实", "理由"), ("判断", "结论", "成立")),
        "这个判断的主要依据是什么？",
    ),
    "dimension_perspective": _family(
        "dimension_perspective",
        (
            "除了你自己，谁的工作会跟着调整？",
            "这些参与者里，谁最先受到影响？",
            "两边目标不同时，你会先照顾哪一方的需要？",
        ),
        (("谁", "参与者", "两边"),),
        "谁的工作会跟着调整？",
    ),
    "dimension_decision": _family(
        "dimension_decision",
        (
            "如果现在开始执行，你会先安排哪一步？",
            "你会怎样安排先后顺序并明确由谁执行？",
            "你准备用什么结果判断这个安排可以继续？",
        ),
        (("安排", "执行"),),
        "你会先安排哪一步？",
    ),
    "dimension_adjustment": _family(
        "dimension_adjustment",
        (
            "这条新信息具体会改变你原安排的哪一部分？",
            "原计划哪些部分保留，哪些部分需要调整？",
            "接下来看到什么结果时，你会再次改变安排？",
        ),
        (("改变", "调整", "保留"), ("安排", "计划", "结果")),
        "你会调整原安排的哪一部分？",
    ),
}


_DIMENSION_FAMILY = {
    "problem_definition": "dimension_problem",
    "evidence_evaluation": "dimension_evidence",
    "reasoning_argumentation": "dimension_reasoning",
    "multiple_perspectives": "dimension_perspective",
    "integrative_decision": "dimension_decision",
    "dynamic_adjustment": "dimension_adjustment",
}

_REPAIR_DIMENSION_FAMILY = {
    "problem_definition": "repair_dimension_problem",
    "evidence_evaluation": "repair_dimension_evidence",
    "reasoning_argumentation": "repair_dimension_reasoning",
    "multiple_perspectives": "repair_dimension_perspective",
    "integrative_decision": "repair_dimension_decision",
    "dynamic_adjustment": "repair_dimension_adjustment",
}


def _compact(value: str | None) -> str:
    return re.sub(r"\s+", "", value or "")


def _contains(text: str, *markers: str) -> bool:
    return any(marker in text for marker in markers)


def _make_binding(
    plan: InterviewPlanOutput,
    family_id: str,
    *,
    mapping_source: str,
    mapping_fields: tuple[str, ...],
) -> IntentBinding:
    family = INTENT_FAMILIES[family_id]
    payload = {
        "registry_version": INTENT_REGISTRY_VERSION,
        "family_id": family.family_id,
        "mapping_source": mapping_source,
        "mapping_fields": mapping_fields,
        "action": plan.action,
        "question_intent": _compact(plan.question_intent),
        "target_evidence": _compact(plan.target_evidence),
        "target_dimension": plan.target_dimension,
        "response_intent": plan.response_intent,
        "release_event_code": plan.release_event_code,
        "candidates": family.candidates,
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return IntentBinding(
        family=family,
        mapping_source=mapping_source,
        mapping_fields=mapping_fields,
        fingerprint=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
    )


def resolve_intent_binding(
    plan: InterviewPlanOutput,
    *,
    pure_authority: bool = False,
    latest_user_text: str | None = None,
) -> IntentBinding | None:
    """Resolve a protected Planner intent to a deterministic semantic family."""

    if plan.action == "CONCLUDE":
        return None
    if pure_authority:
        return _make_binding(
            plan,
            "pure_authority_criteria",
            mapping_source="runtime_special:pure_authority",
            mapping_fields=("question_intent", "response_intent"),
        )

    intent = _compact(plan.question_intent)
    evidence = _compact(plan.target_evidence)
    target = plan.target_dimension
    latest = _compact(latest_user_text)

    if target == "problem_definition" and "延迟" in latest:
        return _make_binding(
            plan,
            "problem_cause_scope",
            mapping_source="surface_topic:problem_cause_scope",
            mapping_fields=("target_dimension", "question_intent"),
        )

    if _contains(intent, "回退") and _contains(intent, "条件", "什么情况"):
        return _make_binding(
            plan,
            "adjustment_rollback_condition",
            mapping_source="intent_rule:adjustment_rollback_condition",
            mapping_fields=("question_intent", "target_evidence", "action"),
        )
    if _contains(intent, "需要看到什么信息", "什么信息才会形成判断"):
        return _make_binding(
            plan,
            "evidence_information_threshold",
            mapping_source="intent_rule:evidence_information_threshold",
            mapping_fields=("question_intent", "target_evidence", "action"),
        )
    if _contains(intent, "兼顾") and _contains(
        intent,
        "进度",
        "返工",
        "风险",
        "两方面",
    ):
        return _make_binding(
            plan,
            "perspective_tradeoff",
            mapping_source="intent_rule:perspective_tradeoff",
            mapping_fields=("question_intent", "target_evidence", "action"),
        )
    if _contains(intent, "协调") and _contains(intent, "双方", "诉求", "关注"):
        return _make_binding(
            plan,
            "perspective_coordination",
            mapping_source="intent_rule:perspective_coordination",
            mapping_fields=("question_intent", "target_evidence", "action"),
        )

    if _contains(intent, "初步决定") or _contains(evidence, "初步决定"):
        return _make_binding(
            plan,
            "decision_initial_choice",
            mapping_source="runtime_special:initial_decision",
            mapping_fields=("question_intent", "target_evidence"),
        )

    if plan.action == "RELEASE_EVENT":
        event_rules = (
            (
                "event_evidence_reassessment",
                target == "evidence_evaluation"
                or _contains(intent, "重新判断", "补充样本", "证据"),
                "event_rule:evidence_reassessment",
            ),
            (
                "event_uncertainty_check",
                plan.release_event_code == "evidence_uncertainty",
                "runtime_event:evidence_uncertainty",
            ),
            (
                "event_stakeholder_priority",
                plan.release_event_code == "stakeholder_conflict",
                "runtime_event:stakeholder_conflict",
            ),
            (
                "event_decision_under_constraint",
                plan.release_event_code == "decision_pressure",
                "runtime_event:decision_pressure",
            ),
            (
                "event_final_plan",
                plan.release_event_code == "integration",
                "runtime_event:integration",
            ),
            (
                "event_priority_adjustment",
                _contains(intent, "优先级", "优先考虑"),
                "event_rule:priority_adjustment",
            ),
            (
                "event_contingency",
                _contains(intent, "替代安排", "备选方案"),
                "event_rule:contingency",
            ),
            (
                "event_process_adjustment",
                _contains(intent, "流程调整"),
                "event_rule:process_adjustment",
            ),
            (
                "event_risk_control",
                _contains(intent, "风险控制"),
                "event_rule:risk_control",
            ),
            (
                "event_adjustment_criteria",
                _contains(intent, "调整依据"),
                "event_rule:adjustment_criteria",
            ),
            (
                "event_scope_adjustment",
                _contains(intent, "试点调整", "试点安排"),
                "event_rule:scope_adjustment",
            ),
            (
                "event_judgment_revision",
                _contains(intent, "改变原判断", "保留还是改变")
                or (plan.release_event_code == "counter_evidence" and target is None),
                "runtime_event:counter_evidence",
            ),
        )
        for family_id, matches, source in event_rules:
            if matches:
                return _make_binding(
                    plan,
                    family_id,
                    mapping_source=source,
                    mapping_fields=(
                        "question_intent",
                        "target_evidence",
                        "target_dimension",
                        "release_event_code",
                    ),
                )
        return _make_binding(
            plan,
            "event_plan_revision",
            mapping_source="event_rule:plan_revision",
            mapping_fields=(
                "question_intent",
                "target_evidence",
                "target_dimension",
                "release_event_code",
            ),
        )

    if plan.action == "INTEGRATE":
        integration_rules = (
            ("integration_monitor_pause", ("监测指标", "暂停条件")),
            ("integration_adjust_review", ("临时调配", "复盘时间")),
            ("integration_launch_gate", ("上线前条件", "推广证据")),
            ("integration_staged_exit", ("分批下单", "取消条件")),
        )
        for family_id, markers in integration_rules:
            if all(marker in intent for marker in markers):
                return _make_binding(
                    plan,
                    family_id,
                    mapping_source=f"intent_rule:{family_id}",
                    mapping_fields=("question_intent", "target_evidence", "action"),
                )
        return _make_binding(
            plan,
            "integration_general",
            mapping_source="runtime_rule:integration_general",
            mapping_fields=("question_intent", "target_evidence", "action"),
        )

    if plan.action == "CLARIFY":
        if plan.response_intent == "clarify_question":
            return _make_binding(
                plan,
                "clarify_restate",
                mapping_source="runtime_special:clarify_question",
                mapping_fields=("question_intent", "response_intent"),
            )
        if plan.response_intent == "explain_term":
            return _make_binding(
                plan,
                "clarify_plain_language",
                mapping_source="runtime_special:explain_term",
                mapping_fields=("question_intent", "response_intent"),
            )
        clarify_rules = (
            ("clarify_observable_metric", ("效果", "可观察")),
            ("clarify_decision_threshold", ("判断阈值",)),
            ("clarify_sample_basis", ("样本和记录",)),
            ("clarify_scope_basis", ("判断依据及具体范围",)),
            ("clarify_agreement_basis", ("成员", "同意")),
            ("reasoning_condition_link", ("成本回收", "成立的条件")),
            ("repair_evidence_criterion", ("退款记录", "判断标准")),
            ("repair_pilot_validation", ("缩小试点", "验证目标")),
            ("repair_cross_validation", ("信息来源", "交叉验证")),
            ("redirect_observable_tradeoff", ("可观察的岗位条件", "取舍")),
            ("redirect_decision_criteria", ("比较两个选项", "关键依据")),
            ("redirect_decision_criteria", ("最重视的判断条件",)),
        )
        for family_id, markers in clarify_rules:
            if all(marker in intent for marker in markers):
                return _make_binding(
                    plan,
                    family_id,
                    mapping_source=f"intent_rule:{family_id}",
                    mapping_fields=(
                        "question_intent",
                        "target_evidence",
                        "response_intent",
                    ),
                )
        if plan.response_intent == "low_information":
            return _make_binding(
                plan,
                "clarify_low_information",
                mapping_source="runtime_special:low_information",
                mapping_fields=("question_intent", "response_intent"),
            )
        if plan.response_intent == "conversation_repair":
            family_id = _REPAIR_DIMENSION_FAMILY.get(
                target or "",
                "repair_dimension_decision",
            )
            return _make_binding(
                plan,
                family_id,
                mapping_source=f"runtime_repair:{target or 'general'}",
                mapping_fields=(
                    "question_intent",
                    "target_evidence",
                    "target_dimension",
                    "response_intent",
                ),
            )
        return _make_binding(
            plan,
            "clarify_restate",
            mapping_source="runtime_rule:clarify_restate",
            mapping_fields=("question_intent", "response_intent"),
        )

    if plan.response_intent == "low_information":
        return _make_binding(
            plan,
            "clarify_low_information",
            mapping_source="runtime_special:low_information",
            mapping_fields=("question_intent", "response_intent"),
        )

    probe_rules = (
        ("evidence_decision_compare", ("比较哪些证据", "更换决定")),
        ("evidence_coverage_gap", ("未覆盖", "影响")),
        ("evidence_validation_method", ("具体证据或方法",)),
        ("decision_reversible_options", ("可逆方案",)),
        ("reasoning_consequence_compare", ("主要后果",)),
        ("decision_continue_stop_rules", ("判断标准", "停止条件")),
        ("perspective_priority_impact", ("提交时间排序", "业务影响")),
        ("evidence_sample_representativeness", ("代表整批", "补充验证")),
        ("reasoning_causal_comparison", ("相关与因果", "比较证据")),
        ("evidence_independent_verification", ("独立数据",)),
        ("evidence_transfer_validity", ("适用于实际运输",)),
        ("perspective_impact_compare", ("不同影响", "一起比较")),
        ("problem_scope", ("最需要解决的问题", "边界")),
        ("problem_scope", ("核心矛盾",)),
        ("problem_scope", ("核心", "边界")),
        ("problem_scope", ("目标", "限制条件")),
        ("problem_scope", ("问题与边界",)),
    )
    for family_id, markers in probe_rules:
        if all(marker in intent for marker in markers):
            return _make_binding(
                plan,
                family_id,
                mapping_source=f"intent_rule:{family_id}",
                mapping_fields=("question_intent", "target_evidence", "action"),
            )

    family_id = _DIMENSION_FAMILY.get(target or "", "dimension_decision")
    source_prefix = (
        "runtime_ending_gap"
        if _contains(intent, "公平作答机会") or _contains(evidence, "结束前")
        else "runtime_dimension"
    )
    return _make_binding(
        plan,
        family_id,
        mapping_source=f"{source_prefix}:{target or 'general'}",
        mapping_fields=("question_intent", "target_evidence", "target_dimension"),
    )


def binding_intent_key(
    plan: InterviewPlanOutput,
    binding: IntentBinding,
) -> str:
    """Keep existing v1.1 audit keys while binding them to richer families."""

    if binding.family.family_id == "decision_initial_choice":
        return "initial_decision"
    if binding.mapping_source.startswith("runtime_ending_gap:"):
        return f"ending_gap_{plan.target_dimension or 'general'}"
    if binding.mapping_source.startswith("runtime_repair:"):
        return f"repair_{plan.target_dimension or 'general'}"
    return binding.family.family_id


def candidate_semantic_errors(
    text: str,
    *,
    binding: IntentBinding,
    stable_order: int,
) -> list[str]:
    errors: list[str] = []
    if stable_order < 0 or stable_order >= len(binding.family.candidates):
        return ["semantic_candidate_order"]
    if text != binding.family.candidates[stable_order]:
        errors.append("semantic_candidate_registry_mismatch")
    for group in binding.family.semantic_groups:
        if not any(marker in text for marker in group):
            errors.append("semantic_anchor_missing")
            break
    return errors


def surface_question_semantic_errors(
    text: str,
    *,
    binding: IntentBinding,
) -> list[str]:
    """Validate a live rephrase without forcing registry wording."""

    errors: list[str] = []
    if text.count("？") + text.count("?") != 1:
        errors.append("surface_question_count")
    for index, group in enumerate(binding.family.semantic_groups):
        if not any(marker in text for marker in group):
            errors.append(f"surface_semantic_anchor_missing:{index}")
    return errors


def semantic_binding_contract_errors(
    plan: InterviewPlanOutput,
    payload: dict[str, object],
) -> list[str]:
    if plan.action == "CONCLUDE":
        candidates = payload.get("question_candidates")
        return [] if candidates == [] else ["conclude_candidate_contract"]

    expected = resolve_intent_binding(
        plan,
        pure_authority=bool(payload.get("pure_authority_request")),
        latest_user_text=(
            str(payload.get("latest_user_text"))
            if payload.get("latest_user_text") is not None
            else None
        ),
    )
    if expected is None:
        return ["missing_expected_intent_binding"]

    errors: list[str] = []
    if payload.get("intent_registry_version") != INTENT_REGISTRY_VERSION:
        errors.append("intent_registry_version_mismatch")
    if payload.get("candidate_intent_key") != binding_intent_key(plan, expected):
        errors.append("candidate_family_resolution_mismatch")
    if payload.get("candidate_mapping_source") != expected.mapping_source:
        errors.append("candidate_mapping_source_mismatch")
    if payload.get("candidate_mapping_fields") != list(expected.mapping_fields):
        errors.append("candidate_mapping_fields_mismatch")
    if payload.get("candidate_mapping_fingerprint") != expected.fingerprint:
        errors.append("candidate_mapping_fingerprint_mismatch")

    rows = payload.get("question_candidates")
    if not isinstance(rows, list) or len(rows) != 3:
        errors.append("candidate_registry_cardinality")
        return list(dict.fromkeys(errors))

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append("candidate_registry_row")
            continue
        if row.get("stable_order") != index:
            errors.append("candidate_registry_order")
        if row.get("intent_family") != expected.family.family_id:
            errors.append("candidate_registry_family")
        if row.get("mapping_fingerprint") != expected.fingerprint:
            errors.append("candidate_registry_fingerprint")
        text = row.get("text")
        if not isinstance(text, str):
            errors.append("candidate_registry_text")
            continue
        errors.extend(
            candidate_semantic_errors(
                text,
                binding=expected,
                stable_order=index,
            )
        )
        claimed = row.get("semantic_contract_codes")
        recomputed = candidate_semantic_errors(
            text,
            binding=expected,
            stable_order=index,
        )
        if claimed != recomputed:
            errors.append("candidate_semantic_audit_mismatch")
    return list(dict.fromkeys(errors))


__all__ = [
    "INTENT_FAMILIES",
    "INTENT_REGISTRY_VERSION",
    "IntentBinding",
    "binding_intent_key",
    "candidate_semantic_errors",
    "resolve_intent_binding",
    "surface_question_semantic_errors",
    "semantic_binding_contract_errors",
]

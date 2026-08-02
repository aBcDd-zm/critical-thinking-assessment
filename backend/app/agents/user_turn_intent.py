from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.agents.schemas import (
    AgentRuntimeContext,
    DialogueTurnContext,
    ResolvedEvidenceItem,
    ResponseCategory,
)


UserTurnIntent = Literal[
    "substantive_answer",
    "clarification_request",
    "term_definition_request",
    "low_information",
    "irrelevant",
]
ProgressiveControlIntent = Literal["request_context", "conversation_repair"]
ConsultativeControlIntent = Literal[
    "clarify_question",
    "explain_term",
    "request_context",
    "conversation_repair",
    "boundary_redirect",
]

LOW_INFORMATION_ANSWERS = {
    "",
    "无",
    "没有",
    "不知道",
    "我不知道",
    "还不知道",
    "暂时不知道",
    "不清楚",
    "我不清楚",
    "不太清楚",
    "不确定",
    "我不确定",
    "随便",
    "没有想法",
    "没有方案",
    "暂无",
    "没想法",
    "没想好",
    "没想清楚",
    "说不上来",
    "我也不知道",
    "none",
    "no",
    "nothing",
}
LOW_INFORMATION_PATTERNS = (
    re.compile(r"^(?:我|我也)?(?:暂时|现在|还|也)?(?:不知道|不清楚|不确定)$"),
    re.compile(r"^(?:我|我也)?(?:还|暂时)?(?:没想法|没想好|没想清楚|说不上来)$"),
)
IRRELEVANT_ANSWERS = {"你好", "您好", "嗨", "hi", "hello", "谢谢", "好的", "好"}
CLARIFICATION_ANSWERS = {
    "我不明白",
    "不明白",
    "我没懂",
    "没懂",
    "我不懂",
    "不懂",
    "没看明白",
    "没听明白",
    "能解释一下吗",
    "可以解释一下吗",
    "请解释一下",
    "能再解释一下吗",
    "可以再说清楚一点吗",
    "具体说说",
    "再具体说说",
    "说具体点",
    "具体一点",
    "你在说什么",
    "你说什么",
    "你什么意思",
}

CLARIFICATION_PATTERNS = (
    re.compile(r"(什么|哪个|哪一个).{0,8}(问题|题目|情境|情况|信息)"),
    re.compile(r"(问题|题目|情境|情况|信息).{0,8}(是什么|有哪些|有什么|没看懂|不明白|不清楚)"),
    re.compile(r"(现在|目前).{0,8}(有什么信息|什么情况|什么问题)"),
    re.compile(
        r"^(?:(?:请|麻烦)(?:你)?|(?:你)?(?:能不能|可以|能))?"
        r"(?:把)?(?:这个|那个|当前|刚才的)?(?:问题|题目|情境)?"
        r"(?:重述|重复|再说)(?:一下|一遍|一次)?"
        r"(?:这个|那个|当前|刚才的)?(?:问题|题目|情境)?(?:吗)?$"
    ),
    re.compile(r"(没听清|听不懂|看不懂|没明白|不明白|没懂|不懂|说简单|简单一点|简单点)"),
    re.compile(r"(没|没有|不).{0,3}(听懂|看懂|明白|理解)"),
    re.compile(r"^(?:这|那|这个|那个)?(?:是)?" r"(?:什么意思|啥意思|怎么理解)(?:呀|啊|呢|吗)?[?？!！。]*$"),
    re.compile(
        r"^(?:(?:请|麻烦)(?:你)?|(?:你)?(?:能不能|可以|能))"
        r".{0,4}(?:解释|说明|讲清楚).{0,4}(?:一下|一点|吗)?$"
    ),
    re.compile(
        r"^(?:(?:请|麻烦)(?:你)?|(?:你)?(?:能不能|可以|能))?"
        r"(?:再)?(?:具体|详细|展开)(?:说说|说|讲讲|讲|解释)"
        r"(?:一下|一点)?(?:吗)?[?？!！。]*$"
    ),
    re.compile(
        r"^(?:你)?(?:刚才|前面)?(?:在)?说(?:的)?(?:是)?什么"
        r"(?:意思)?(?:呢|啊|呀|吗)?[?？!！。]*$"
    ),
    re.compile(
        r"^(?:你)?(?:刚才|前面)?说的[“\"'‘]"
        r"[^”\"'’]{1,80}[”\"'’](?:是)?"
        r"(?:什么意思|指什么|怎么理解)?[?？!！。]*$"
    ),
    re.compile(
        r"^(?:你)?(?:刚才|前面)?说的.{2,80}"
        r"(?:什么|哪一点|哪部分|哪一边|哪方面)$"
    ),
    re.compile(
        r"(?:没跟上|没听明白|没听清).{0,10}"
        r"(?:在问|问的是).{0,8}(?:什么|哪件事|哪一点)"
    ),
    re.compile(
        r"^(?:你)?(?:刚才|前面)?(?:说的)?(?:这|那)?"
        r"(?:句|句话|个问题)(?:是)?(?:什么意思|指什么|怎么理解)"
        r"(?:呢|啊|吗)?[?？!！。]*$"
    ),
)
TERM_PATTERN = re.compile(
    r"^(?:(?:请问|我想问|你说的|题目里的|这里的|这个|那个))?"
    r"(?P<term>[A-Za-z0-9一-龥、和与]{1,20}?)(?:具体)?"
    r"(?:是什么(?:东西)?|是什么意思|什么意思|啥意思|怎么理解)(?:吗)?$"
)
TERM_PREFIX_PATTERN = re.compile(
    r"^(?:(?:请|麻烦)(?:你)?|(?:你)?(?:能不能|可以|能))?"
    r"(?:帮我)?(?:什么叫|解释一下|解释|说明一下)"
    r"(?P<term>[A-Za-z0-9一-龥、和与]{1,20}?)(?:吗)?$"
)
STAGE_SKIP_PATTERN = re.compile(r"^(下一题|下一阶段|跳过|跳过本题|先跳过|先跳过本题|换一题|进入下一阶段|不答了)$")
EVIDENCE_BOUNDARY_PATTERNS = (
    re.compile(r"(信息|线索|数据|证据).{0,6}(不足|不够|没有|缺少|有限)"),
    re.compile(r"(没有|缺少).{0,8}(信息|线索|数据|证据)"),
    re.compile(r"(不能|无法|不好|没法).{0,6}(判断|下结论|确定)"),
    re.compile(r"(只凭|仅凭).{0,12}(不能|无法|不好).{0,6}(判断|下结论|确定)"),
)

PROGRESSIVE_CONTEXT_PATTERNS = (
    re.compile(r"(眼下|现在|目前).{0,8}(是什么情况|什么情况|有哪些情况)"),
    re.compile(r"(新安排|原安排|新旧安排).{0,8}(是什么|分别是什么|有什么区别)"),
    re.compile(r"(两个|两方|不同).{0,6}(诉求|立场).{0,8}(是什么|有哪些|说清楚)"),
    re.compile(
        r"^(?:能不能|可以|请|麻烦)?(?:先|再)?(?:多)?"
        r"(?:给|说|提供|补充)(?:我)?(?:一点|点|些|一些|更多)?"
        r"(?:现有|现在|目前|当前|已知)?(?:的)?(?:信息|情况|线索)(?:吗)?$"
    ),
    re.compile(
        r"^(?:能不能|能否|能|可以|请|麻烦)?(?:先)?(?:把)?"
        r"(?:现有|现在|目前|当前|已知)(?:的)?"
        r"(?:信息|情况|线索)(?:再)?"
        r"(?:说清楚|说明|讲清楚|梳理|汇总|回顾|总结|列一下)"
        r"(?:一点|一下)?(?:吗)?$"
    ),
    re.compile(
        r"^(?:能不能|能否|能|可以|请|麻烦)?(?:先|再)?"
        r"(?:帮我)?(?:梳理|汇总|回顾|总结|列一下)(?:一点|一下)?"
        r"(?:现有|现在|目前|当前|已知|已经知道|目前已有)(?:的)?"
        r"(?:信息|情况|线索)(?:吗)?$"
    ),
    re.compile(
        r"^(?:再)?(?:说|提供|补充)(?:一些|些)?"
        r"(?:现在|目前|当前)?(?:已经)?(?:知道|确定)(?:的)?"
        r"(?:信息|情况|线索)(?:吗)?$"
    ),
    re.compile(r"^(?:还|另外)?(?:有)?(?:什么|哪些|更多)(?:信息|情况|线索)(?:吗)?$"),
)

META_CLARIFICATION_PATTERNS = (
    re.compile(
        r"(?:我问的是|我是问|我想问的是).{0,12}"
        r"(?:这|那|这句|那句|这句话|那句话|你刚才|你上一句|上一个问题)"
        r".{0,10}(?:什么意思|在问什么|指什么|怎么理解)"
    ),
    re.compile(
        r"^(?:这|那)(?:句|句话|个问题)(?:具体)?(?:是)?"
        r"(?:什么意思|指什么|怎么理解)(?:吗)?$"
    ),
    re.compile(
        r"(?:不是问|我不是在问).{0,12}(?:答案|怎么选|谁)"
        r".{0,16}(?:没懂|不懂|不明白|想问).{0,12}(?:问题|这句话|什么意思)"
    ),
)
PROGRESSIVE_REPAIR_PATTERNS = (
    re.compile(r"(已经|刚才|前面|上面|之前).{0,12}(回答|答过|说过|提过|讲过)"),
    re.compile(r"(这个|该|同一个|刚才的)?(问题|题目|追问).{0,8}(重复|问过|又问|再问)"),
    re.compile(r"(怎么|为什么).{0,6}(又|还|再).{0,3}(问|重复)"),
    re.compile(r"(别|不要|不用|无需).{0,6}(再问|重复问|重复提问)"),
    re.compile(r"(我不是|不是).{0,4}(已经)?(回答|答过|说过)"),
    re.compile(r"(请|麻烦)?(换个|换一个|换种).{0,6}(角度|问题|问法|方式)"),
    re.compile(
        r"^(?:我)?(?:都|已经)?(?:说了|讲了|回答了|答了)"
        r"[^，,；;。.!！？?\n]{2,40}$"
    ),
    re.compile(
        r"^(?:不是)?(?:都)?(?:说过|讲过|回答过|答过)"
        r"[^，,；;。.!！？?\n]{2,40}$"
    ),
)

HUMANISTIC_BOUNDARY_REQUEST_PATTERNS = (
    re.compile(
        r"(?:你|AI).{0,6}(?:能不能|可以|愿意|来)?"
        r"(?:当|做|成为).{0,3}(?:我|我的)"
        r"(?:父亲|母亲|爸爸|妈妈|伴侣|爱人|男友|女友|朋友|家人|心理咨询师|治疗师)"
    ),
    re.compile(r"(?:把你当|你就是|你来当).{0,4}" r"(?:父亲|母亲|爸爸|妈妈|伴侣|爱人|朋友|家人|心理咨询师|治疗师)"),
    re.compile(r"(?:作为|以).{0,4}(?:专家|权威|心理咨询师).{0,10}" r"(?:告诉我|替我|帮我决定|给答案|选哪个)"),
    re.compile(r"(?:你替我决定|直接告诉我选哪个|如果是你会选哪个|你支持我选)"),
    re.compile(
        r"(?:(?:你|AI)?(?:直接)?(?:告诉我|给我|说出))"
        r"(?:一个)?(?:标准|正确|参考)?答案"
        r"(?:应该)?(?:是什么|怎么选|选哪个|哪一个)?"
    ),
    re.compile(r"(?:你自己|你本人|你以前|你小时候|你的私人经历).{0,12}" r"(?:怎么做|经历过|发生过|是什么|怎样)"),
)
HUMANISTIC_V1_1_AUTHORITY_REQUEST_PATTERNS = (
    re.compile(
        r"(?:(?:你|AI)\s*)?(?:(?:能不能|可以|请|来)\s*)?"
        r"(?:帮|替)我(?:做(?:个|这个)?决定|作决定|决定(?:一下)?|"
        r"选择|选(?:一个|哪个|哪一个)?)"
        r"(?:(?!(?:但|不过|同时|然后|而且|所以))"
        r"[^，,；;。.!！？?\n]){0,18}"
    ),
    re.compile(
        r"(?:请\s*)?(?:直接\s*)?(?:告诉我|给我)"
        r"(?:一个|明确的?)?(?:答案|结论|选哪个|怎么选|怎么做)"
        r"(?:吧|吗|呢)?"
    ),
    re.compile(r"直接给(?:我)?(?:一个|明确的?)?答案(?:吧|吗|呢)?"),
    re.compile(
        r"(?:(?:请)?(?:你|AI)?(?:直接)?(?:告诉|建议)(?:我)?|"
        r"你觉得|你认为|你建议|你说)(?:我)?(?:到底)?"
        r"(?:应该|该|最好)(?:怎么办|怎么做|怎么选|如何选|选哪个|"
        r"选择哪个|上线还是延期|延期还是上线|"
        r"[^，,；;。.!！？?\n]{1,18}还是[^，,；;。.!！？?\n]{1,18})"
        r"(?:吧|吗|呢)?"
    ),
    re.compile(
        r"我(?:到底)?(?:应该|该)(?:怎么办|怎么做|怎么选|如何选|"
        r"选哪个|选择哪个|上线还是延期|延期还是上线|"
        r"[^，,；;。.!！？?\n]{1,18}还是[^，,；;。.!！？?\n]{1,18})"
        r"(?:吧|吗|呢)?"
    ),
    re.compile(
        r"如果是你(?:会|要|将)(?:怎么办|怎么做|怎么选|如何选|"
        r"选哪个|选择哪个|选哪一个|"
        r"[^，,；;。.!！？?\n]{1,18}还是[^，,；;。.!！？?\n]{1,18})"
        r"(?:吧|吗|呢)?"
    ),
)
_AUTHORITY_CLAUSE_SPLIT_RE = re.compile(r"[；;。.!！？?\n]+")
_SUBSTANTIVE_AUTHORITY_MIX_RE = re.compile(
    r"(?:我(?:倾向|选择|决定|会|认为|判断|计划|建议)|"
    r"因为|依据|风险|数据|信息|证据|先|如果|延期|上线|灰度|回滚|"
    r"客户|用户|研发|市场|团队|人手|资源|预算|成本|时间|进度|质量|"
    r"故障|日志|阈值|返工|测试|方案|安排|核实|确认|优先|影响)"
)
_CHOICE_COMPARISON_FRAGMENT_RE = re.compile(
    r"(?:"
    r"(?:方案|选项)?[A-Za-z甲乙丙一二三123](?:方案|选项)?"
    r".{0,8}(?:更|较为|比较)(?:稳妥|合适|可行|安全|重要|优先|好)|"
    r"(?:我的?)?(?:首选|第一选择|优先选择)(?:是|为)?"
    r"[A-Za-z甲乙丙一二三123]|"
    r"我(?:想选|会选|更倾向|更看好)"
    r"[A-Za-z甲乙丙一二三123]"
    r")"
)


@dataclass(frozen=True)
class HumanisticAuthorityRequest:
    kind: Literal["pure", "mixed"]
    substantive_text: str | None = None
    substantive_fragments: tuple[str, ...] = ()
    authority_spans: tuple[str, ...] = ()


RELEVANCE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "s1_problem_definition": (
        "决定",
        "决策",
        "判断",
        "原因",
        "下降",
        "弄清",
        "上线",
        "延期",
        "质量",
        "风险",
        "时间",
        "同步",
        "是否",
        "范围",
        "压缩",
        "完成",
        "效率",
    ),
    "s2_evidence_verification": (
        "查",
        "核实",
        "数据",
        "样本",
        "用户",
        "机型",
        "网络",
        "日志",
        "失败",
        "反馈",
        "来源",
        "比例",
        "复现",
        "可靠",
    ),
    "s3_stakeholder_perspectives": (
        "市场",
        "研发",
        "运营",
        "用户",
        "团队",
        "诉求",
        "影响",
        "利益",
        "冲突",
        "优先",
    ),
    "s4_reasoning_decision": (
        "上线",
        "延期",
        "少量",
        "灰度",
        "方案",
        "选择",
        "因为",
        "依据",
        "风险",
        "收益",
    ),
    "s5_dynamic_adjustment": (
        "改变",
        "调整",
        "坚持",
        "暂停",
        "延期",
        "上线",
        "因为",
        "风险",
        "监控",
        "不变",
    ),
    "s6_integrated_plan": (
        "上线",
        "延期",
        "灰度",
        "少量",
        "计划",
        "步骤",
        "负责",
        "监控",
        "回滚",
        "兜底",
        "沟通",
    ),
}

EVIDENCE_KEYWORDS: dict[str, dict[str, tuple[str, ...]]] = {
    "s1_problem_definition": {
        "核心判断": ("决定", "决策", "判断", "原因", "为什么", "是否", "弄清"),
        "核心问题": ("决定", "决策", "是否", "上线", "延期", "压缩", "完成", "效率"),
        "限制条件": ("时间", "质量", "风险", "资源", "样本", "范围", "数据", "信息", "人手", "预算"),
        "约束条件": ("48", "时间", "质量", "风险", "资源", "窗口", "反馈", "同步"),
        "决策边界": ("是否", "何时", "范围", "压缩", "全部", "少量", "上线", "延期"),
    },
    "s2_evidence_verification": {
        "证据来源": ("日志", "反馈", "数据", "研发", "用户", "监控", "报告"),
        "样本范围": ("样本", "机型", "网络", "用户", "比例", "范围", "设备", "弱网"),
        "可靠性判断": (
            "复现",
            "对比",
            "交叉",
            "验证",
            "可靠",
            "真实",
            "准确",
            "重复",
            "线索不足",
            "信息不足",
            "不能判断",
            "无法判断",
        ),
    },
    "s3_stakeholder_perspectives": {
        "利益相关方": ("市场", "研发", "运营", "用户", "团队"),
        "视角冲突": ("冲突", "影响", "担心", "诉求", "反对", "风险"),
        "取舍依据": ("优先", "权衡", "取舍", "理由", "因为", "依据"),
    },
    "s4_reasoning_decision": {
        "决策方案": ("上线", "延期", "少量", "灰度", "暂停"),
        "推理链": ("因为", "所以", "证据", "风险", "收益", "依据"),
        "关键假设": ("如果", "前提", "假设", "条件", "只要"),
    },
    "s5_dynamic_adjustment": {
        "是否调整判断": ("改变", "调整", "坚持", "不变", "延期", "暂停", "上线"),
        "调整理由": ("因为", "所以", "风险", "信息", "失败", "影响"),
        "后续监控条件": ("监控", "指标", "条件", "阈值", "数据", "达到", "一旦"),
    },
    "s6_integrated_plan": {
        "最终方案": ("上线", "延期", "少量", "灰度", "暂停"),
        "执行步骤": ("先", "然后", "步骤", "负责", "安排", "时间", "沟通"),
        "风险兜底": ("风险", "回滚", "停止", "暂停", "兜底", "预案", "一旦"),
    },
}

TERM_EXPLANATIONS = {
    "决策": "决策就是在几个做法中选一个。例如这里要选按时上线、延期，还是先让少量用户使用。",
    "灰度上线": "灰度上线就是先让少量用户使用，确认稳定后再逐步开放给更多用户。",
    "86条和19条": "86条是收到的全部内测反馈，19条是其中提到任务同步失败的反馈。",
    "内测反馈": "内测反馈是产品正式上线前，测试用户使用后报告的问题和感受。",
    "同步失败": "同步失败是用户做的任务或修改没有正常传到其他设备或其他成员那里。",
}


def classify_user_turn(text: str) -> UserTurnIntent:
    normalized = _normalize(text)
    references_interview_question = any(
        marker in normalized
        for marker in (
            "你刚才问的是",
            "你问的是",
            "刚才的问题是",
        )
    )
    asks_to_disambiguate = any(marker in normalized for marker in ("还是", "或者", "或是"))
    if references_interview_question and asks_to_disambiguate:
        return "clarification_request"
    if classify_progressive_control_intent(text) is not None:
        return "clarification_request"
    if any(pattern.search(normalized) for pattern in META_CLARIFICATION_PATTERNS):
        return "clarification_request"
    if normalized in CLARIFICATION_ANSWERS or any(
        pattern.search(normalized) for pattern in CLARIFICATION_PATTERNS
    ):
        return "clarification_request"
    if _extract_term(text):
        return "term_definition_request"
    if normalized in IRRELEVANT_ANSWERS:
        return "irrelevant"
    if normalized in LOW_INFORMATION_ANSWERS or any(
        pattern.fullmatch(normalized) for pattern in LOW_INFORMATION_PATTERNS
    ):
        return "low_information"
    return "substantive_answer"


def classify_progressive_control_intent(
    text: str,
) -> ProgressiveControlIntent | None:
    """Identify non-scoring context/repair turns before any model interpretation.

    The patterns deliberately require references to the interview/question itself.
    This prevents substantive phrases such as ``重复测试`` from being mistaken for
    a complaint about repeated questioning.
    """
    normalized = _normalize(text)
    explicit_context_questions = (
        "现在有哪些信息是已经确定的",
        "目前有哪些信息是已经确定的",
        "现在有哪些信息已经确定",
        "目前有哪些信息已经确定",
        "现在已确定的信息有哪些",
        "目前已确定的信息有哪些",
        "现在我们已经知道哪些信息",
        "目前我们已经知道哪些信息",
    )
    if any(marker in normalized for marker in explicit_context_questions):
        return "request_context"
    if any(pattern.search(normalized) for pattern in PROGRESSIVE_CONTEXT_PATTERNS):
        return "request_context"
    if any(pattern.search(normalized) for pattern in PROGRESSIVE_REPAIR_PATTERNS):
        return "conversation_repair"
    if normalized in {
        "什么玩意",
        "我已经回答过",
        "问过了",
        "问题重复",
        "一直重复",
        "莫名其妙",
        "听不懂你在问什么",
    }:
        return "conversation_repair"
    return None


def classify_consultative_control_intent(
    text: str,
) -> ConsultativeControlIntent | None:
    """Return deterministic non-scoring intent for consultative interviews."""
    normalized = _normalize(text)
    if any(
        pattern.search(normalized) for pattern in HUMANISTIC_BOUNDARY_REQUEST_PATTERNS
    ):
        return "boundary_redirect"
    progressive_intent = classify_progressive_control_intent(text)
    if progressive_intent is not None:
        return progressive_intent

    user_intent = classify_user_turn(text)
    return {
        "clarification_request": "clarify_question",
        "term_definition_request": "explain_term",
    }.get(user_intent)


def analyze_humanistic_authority_request(
    text: str,
) -> HumanisticAuthorityRequest | None:
    """Classify v1.1 decision-substitution requests without discarding an answer.

    This is intentionally separate from the legacy control-intent classifier so
    baseline_v1 and humanistic_v1 keep their frozen routing behavior.
    """

    matches = [
        match
        for pattern in HUMANISTIC_V1_1_AUTHORITY_REQUEST_PATTERNS
        for match in pattern.finditer(text)
    ]
    if not matches:
        return None

    authority_intervals = _merge_authority_intervals(matches)
    substantive_fragments: list[str] = []
    cursor = 0
    for start, end in authority_intervals:
        substantive_fragments.extend(
            _substantive_fragments_from_gap(text[cursor:start])
        )
        cursor = end
    substantive_fragments.extend(_substantive_fragments_from_gap(text[cursor:]))
    authority_spans = tuple(text[start:end] for start, end in authority_intervals)
    if substantive_fragments:
        fragments = tuple(substantive_fragments)
        return HumanisticAuthorityRequest(
            kind="mixed",
            substantive_text="；".join(fragments),
            substantive_fragments=fragments,
            authority_spans=authority_spans,
        )
    return HumanisticAuthorityRequest(
        kind="pure",
        authority_spans=authority_spans,
    )


def _merge_authority_intervals(
    matches: list[re.Match[str]],
) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for match in sorted(matches, key=lambda item: (item.start(), -item.end())):
        start, end = match.span()
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
            continue
        merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def _substantive_fragments_from_gap(gap: str) -> list[str]:
    fragments: list[str] = []
    for raw_fragment in _AUTHORITY_CLAUSE_SPLIT_RE.split(gap):
        cleaned = raw_fragment.strip(" \t\r\n，,；;。.!！？?")
        cleaned = re.sub(
            r"^(?:但|不过|那么|所以|然后|同时|而且)\s*",
            "",
            cleaned,
            count=1,
        ).strip(" \t\r\n，,；;。.!！？?")
        cleaned = re.sub(
            r"\s*(?:但|不过|那么|所以|然后|同时|而且)$",
            "",
            cleaned,
            count=1,
        ).strip(" \t\r\n，,；;。.!！？?")
        if cleaned and (
            _SUBSTANTIVE_AUTHORITY_MIX_RE.search(cleaned)
            or _CHOICE_COMPARISON_FRAGMENT_RE.search(cleaned)
        ):
            fragments.append(cleaned)
    return fragments


def analyze_user_turn(context: AgentRuntimeContext, text: str) -> dict:
    intent = classify_user_turn(text)
    normalized = _normalize(text)
    term = _extract_term(text)
    needs_plain = intent in {
        "clarification_request",
        "term_definition_request",
    } and bool(re.search(r"没|不明白|不懂|什么意思|啥意思|简单|怎么理解|是什么东西|什么叫|解释", text))
    target = "term" if intent == "term_definition_request" else "last_question"
    if intent == "clarification_request" and re.search(r"情境|信息|题目|什么问题|有哪些", text):
        target = "stage_question"

    if intent != "substantive_answer":
        relevance = "not_applicable"
        evidence_keys: list[str] = []
    else:
        keywords = RELEVANCE_KEYWORDS.get(context.stage.stage_code, ())
        evidence_boundary = (
            context.stage.stage_code == "s2_evidence_verification"
            and is_evidence_boundary_expression(text)
        )
        duplicate_prior_stage = any(
            turn.speaker == "user"
            and turn.stage_code != context.stage.stage_code
            and _normalize(turn.content) == normalized
            for turn in context.dialogue_history
            if turn.content != text
            or turn.turn_id != getattr(context.latest_user_turn, "turn_id", None)
        )
        relevant = evidence_boundary or any(
            keyword in normalized for keyword in keywords
        )
        relevance = "off_topic" if duplicate_prior_stage or not relevant else "relevant"
        evidence_keys = (
            []
            if relevance == "off_topic"
            else _detect_evidence_keys(context.stage.stage_code, normalized)
        )
        if evidence_boundary and "可靠性判断" not in evidence_keys:
            evidence_keys.append("可靠性判断")

    response_category = _response_category(intent, relevance)
    return {
        "intent": intent,
        "relevance": relevance,
        "response_category": response_category,
        "evidence_keys": evidence_keys,
        "needs_plain_language": needs_plain,
        "clarification_target": target
        if intent in {"clarification_request", "term_definition_request"}
        else None,
        "term": term if intent == "term_definition_request" else None,
        "confidence": 0.9 if intent != "substantive_answer" or evidence_keys else 0.72,
    }


def current_stage_user_turns(context: AgentRuntimeContext) -> list[DialogueTurnContext]:
    return [
        turn
        for turn in context.dialogue_history
        if turn.speaker == "user" and turn.stage_code == context.stage.stage_code
    ]


def evidence_coverage(context: AgentRuntimeContext) -> dict[str, str]:
    expected = list(context.stage.exit_criteria.get("expected_evidence") or [])
    coverage = {str(item): "missing" for item in expected}
    latest_snapshot = next(
        (
            turn.analysis_json.get("resolved_evidence_snapshot")
            for turn in reversed(current_stage_user_turns(context))
            if turn.analysis_json
            and isinstance(turn.analysis_json.get("resolved_evidence_snapshot"), list)
        ),
        None,
    )
    resolved_keys: set[str] = set()
    if latest_snapshot is not None:
        for item in latest_snapshot:
            if not isinstance(item, dict):
                continue
            key = str(item.get("evidence_key") or "")
            state = item.get("coverage")
            if key not in coverage or state not in {"covered", "partial", "missing"}:
                continue
            coverage[key] = "complete" if state == "covered" else state
            resolved_keys.add(key)

    for turn in current_stage_user_turns(context):
        analysis = turn.analysis_json or analyze_user_turn(context, turn.content)
        if not is_scoring_analysis(analysis, text=turn.content):
            continue
        for key in analysis.get("evidence_keys") or []:
            if key in coverage and key not in resolved_keys:
                coverage[key] = "complete"
    return coverage


def missing_evidence(context: AgentRuntimeContext) -> list[str]:
    return [
        key for key, state in evidence_coverage(context).items() if state != "complete"
    ]


def has_substantive_stage_answer(context: AgentRuntimeContext) -> bool:
    return any(
        is_scoring_analysis(
            turn.analysis_json or analyze_user_turn(context, turn.content),
            text=turn.content,
        )
        for turn in current_stage_user_turns(context)
    )


def is_scoring_analysis(
    analysis: dict | None,
    *,
    text: str | None = None,
) -> bool:
    if text and classify_progressive_control_intent(text) is not None:
        return False
    if not analysis:
        return False
    if analysis.get("analysis_source") in {
        "consultative_turn_v3_2",
        "consultative_turn_v3_3",
    }:
        return (
            analysis.get("formal_answer") is True
            and analysis.get("response_intent") == "assess_answer"
            and analysis.get("excluded_from_scoring") is not True
        )
    if any(
        isinstance(item, dict) and item.get("coverage") == "covered"
        for item in analysis.get("resolved_evidence") or []
    ):
        return True
    local_category = analysis.get("response_category")
    resolved_category = analysis.get("resolved_response_category")
    if resolved_category is not None:
        return resolved_category == "assess_answer"
    return (
        analysis.get("intent") == "substantive_answer"
        and analysis.get("relevance") != "off_topic"
        and local_category in {None, "assess_answer"}
    )


def is_stage_skip_request(text: str) -> bool:
    return bool(STAGE_SKIP_PATTERN.fullmatch(_normalize(text)))


def is_evidence_boundary_expression(text: str) -> bool:
    normalized = _normalize(text)
    return any(pattern.search(normalized) for pattern in EVIDENCE_BOUNDARY_PATTERNS)


def validate_resolved_evidence(
    context: AgentRuntimeContext,
    items: list[ResolvedEvidenceItem],
) -> tuple[list[ResolvedEvidenceItem], list[str]]:
    expected = {
        str(item) for item in context.stage.exit_criteria.get("expected_evidence") or []
    }
    valid_turn_indexes = {
        turn.turn_index
        for turn in current_stage_user_turns(context)
        if turn.turn_index is not None
    }
    validated_by_key: dict[str, ResolvedEvidenceItem] = {}
    warnings: list[str] = []

    for item in items:
        if item.evidence_key not in expected:
            warnings.append(
                f"ignored semantic evidence outside current stage: {item.evidence_key}"
            )
            continue
        supporting_indexes = [
            index
            for index in dict.fromkeys(item.supporting_turn_indexes)
            if index in valid_turn_indexes
        ]
        if item.coverage == "covered" and not supporting_indexes:
            warnings.append(
                f"ignored covered evidence without a valid supporting turn: {item.evidence_key}"
            )
            continue
        validated_by_key[item.evidence_key] = item.model_copy(
            update={"supporting_turn_indexes": supporting_indexes}
        )

    return (
        [
            validated_by_key[key]
            for key in context.stage.exit_criteria.get("expected_evidence") or []
            if key in validated_by_key
        ],
        warnings,
    )


def apply_model_resolution_to_context(
    context: AgentRuntimeContext,
    *,
    resolved_response_category: ResponseCategory,
    resolved_evidence: list[ResolvedEvidenceItem],
) -> AgentRuntimeContext:
    resolved_context = context.model_copy(deep=True)
    latest_index = (
        resolved_context.latest_user_turn.turn_index
        if resolved_context.latest_user_turn
        else None
    )
    provided_keys = {item.evidence_key for item in resolved_evidence}
    previous_snapshot = next(
        (
            turn.analysis_json.get("resolved_evidence_snapshot")
            for turn in reversed(current_stage_user_turns(resolved_context))
            if turn.turn_index != latest_index
            and turn.analysis_json
            and isinstance(turn.analysis_json.get("resolved_evidence_snapshot"), list)
        ),
        [],
    )
    snapshot_by_key = {
        item.get("evidence_key"): item
        for item in previous_snapshot
        if isinstance(item, dict) and item.get("evidence_key")
    }
    snapshot_by_key.update(
        {item.evidence_key: item.model_dump(mode="json") for item in resolved_evidence}
    )
    snapshot = [
        snapshot_by_key[key]
        for key in resolved_context.stage.exit_criteria.get("expected_evidence") or []
        if key in snapshot_by_key
    ]

    for turn in resolved_context.dialogue_history:
        if (
            turn.speaker != "user"
            or turn.stage_code != resolved_context.stage.stage_code
        ):
            continue
        analysis = dict(
            turn.analysis_json or analyze_user_turn(resolved_context, turn.content)
        )
        previous = [
            item
            for item in analysis.get("resolved_evidence") or []
            if isinstance(item, dict) and item.get("evidence_key") not in provided_keys
        ]
        attached = [
            item.model_dump(mode="json")
            for item in resolved_evidence
            if turn.turn_index in item.supporting_turn_indexes
        ]
        analysis["resolved_evidence"] = previous + attached
        if turn.turn_index == latest_index:
            analysis["resolved_response_category"] = resolved_response_category
            analysis["resolved_evidence_snapshot"] = snapshot
        turn.analysis_json = analysis

    if resolved_context.latest_user_turn:
        matching = next(
            (
                turn
                for turn in resolved_context.dialogue_history
                if turn.turn_index == latest_index
            ),
            None,
        )
        if matching is not None:
            resolved_context.latest_user_turn.analysis_json = matching.analysis_json
    return resolved_context


def build_clarification_response(context: AgentRuntimeContext) -> str:
    latest_text = context.latest_user_turn.content if context.latest_user_turn else ""
    if re.search(
        r"说简单|简单一点|简单点|听不懂|没听懂|看不懂|不明白|换个说法|拆开|细化",
        latest_text,
    ):
        dynamic = next(
            (
                turn.content
                for turn in reversed(context.dialogue_history)
                if turn.stage_code == context.stage.stage_code
                and turn.content_type == "dynamic_info"
            ),
            None,
        )
        question = _plain_question(context.stage.stage_code)
        dynamic_prefix = f"新信息：{dynamic}\n\n" if dynamic else ""
        return f"我换成简单说法。\n\n{dynamic_prefix}现在只回答这一点：{question}"

    previous_ai = _latest_question_turn(context)
    if previous_ai and previous_ai.content_type not in {"stage_question"}:
        return f"我重新说一下刚才的问题。\n\n{_simplify_text(previous_ai.content, context.stage.stage_code)}"
    return (
        "我把这道题分成三部分说明。\n\n"
        f"【发生了什么】\n{context.scenario.background}\n\n"
        f"【现在知道什么】\n{context.stage.context}\n\n"
        f"【现在只回答什么】\n{_plain_question(context.stage.stage_code)}"
    )


def build_term_explanation(context: AgentRuntimeContext, term: str | None) -> str:
    normalized_term = _normalize(term or "")
    explanation = next(
        (
            value
            for key, value in TERM_EXPLANATIONS.items()
            if _normalize(key) in normalized_term or normalized_term in _normalize(key)
        ),
        None,
    )
    if explanation is None:
        explanation = f"“{term or '这个词'}”指的是题目里需要你理解的这个概念。你可以先按日常意思理解。"
    return f"{explanation}\n\n现在只回答这一点：{_plain_question(context.stage.stage_code)}"


def build_guidance_response(context: AgentRuntimeContext) -> str:
    openers = (
        "先说你的第一反应就可以。",
        "不用一次想完整，先说你最在意的一点。",
        "可以先说你会怎么做，我们再慢慢展开。",
    )
    count = sum(
        1
        for turn in context.dialogue_history
        if turn.speaker == "ai"
        and turn.stage_code == context.stage.stage_code
        and turn.content_type == "guidance_response"
    )
    return f"{openers[count % len(openers)]}\n\n{_plain_question(context.stage.stage_code)}"


def build_redirect_response(context: AgentRuntimeContext) -> str:
    return "我们先回到眼前这一步。\n\n" f"{_plain_question(context.stage.stage_code)}"


def build_stage_incomplete_prompt(missing: list[str]) -> str:
    labels = "、".join(missing)
    return f"这一题还缺少这些内容：{labels}。你可以选择继续补充，或者跳过本题。"


def build_missing_evidence_question(stage_code: str, evidence: str) -> str:
    questions = {
        "核心问题": "如果只能先解决一件事，你会选哪一件？",
        "约束条件": "现实中最限制这个决定的是什么？",
        "决策边界": "这次安排里，哪一点不能退让？",
        "证据来源": "你会先从哪里找到相关信息？",
        "样本范围": "你会重点看哪些用户或使用环境？",
        "可靠性判断": "你怎么确认这些信息值得相信？",
        "利益相关方": "这个决定会直接影响谁？",
        "视角冲突": "这些人的目标冲突在哪里？",
        "取舍依据": "你按什么标准决定先照顾谁？",
        "决策方案": "如果现在要拍板，你会怎么安排上线？",
        "推理链": "你这样选择，最主要的理由是什么？",
        "关键假设": "这个方案要成立，最依赖什么条件？",
        "是否调整判断": "看到新信息后，你会改变原来的安排吗？",
        "调整理由": "这条新信息为什么会影响你的选择？",
        "后续监控条件": "接下来你最想盯住哪一个变化？",
        "最终方案": "你最后会怎么安排上线？",
        "执行步骤": "如果明天开始执行，第一步做什么？",
        "风险兜底": "出现什么情况时，你会暂停或回退？",
    }
    return questions.get(evidence, f"请补充说明“{evidence}”。")


def _detect_evidence_keys(stage_code: str, normalized: str) -> list[str]:
    mapping = EVIDENCE_KEYWORDS.get(stage_code, {})
    return [
        key
        for key, keywords in mapping.items()
        if any(word in normalized for word in keywords)
    ]


def _response_category(intent: UserTurnIntent, relevance: str) -> ResponseCategory:
    if intent == "clarification_request":
        return "clarify_question"
    if intent == "term_definition_request":
        return "explain_term"
    if intent in {"low_information", "irrelevant"}:
        return "encourage_answer"
    if relevance == "off_topic":
        return "redirect"
    return "assess_answer"


def _extract_term(text: str) -> str | None:
    normalized = _normalize(text)
    match = TERM_PREFIX_PATTERN.search(normalized) or TERM_PATTERN.search(normalized)
    if not match:
        return None
    term = match.group("term")
    term = re.sub(r"^(请问|那|这个|这里的|题目里的)", "", term)
    if any(
        marker in term
        for marker in (
            "我问的是",
            "我是问",
            "我想问",
            "这句话",
            "那句话",
            "你刚才",
            "你上一句",
            "上一个问题",
        )
    ):
        return None
    if term in {"这", "那", "这句", "那句", "这个问题", "那个问题"}:
        return None
    return term or None


def _latest_question_turn(context: AgentRuntimeContext) -> DialogueTurnContext | None:
    candidates = [
        turn
        for turn in reversed(context.dialogue_history)
        if turn.speaker == "ai"
        and turn.stage_code == context.stage.stage_code
        and turn.content_type
        in {
            "stage_question",
            "followup_question",
            "stage_incomplete_prompt",
            "interview_opening",
            "interview_event",
            "interview_followup",
            "interview_integration",
            "interview_clarification",
        }
    ]
    return next(
        (
            turn
            for turn in candidates
            if turn.content_type != "interview_clarification"
        ),
        candidates[0] if candidates else None,
    )


def _plain_question(stage_code: str) -> str:
    return {
        "s1_problem_definition": "如果现在由你负责，你会先把哪件事定下来？",
        "s2_evidence_verification": "要判断同步失败严不严重，你第一步会查什么？",
        "s3_stakeholder_perspectives": "四方意见不一样时，你会先处理谁的担忧？",
        "s4_reasoning_decision": "现在由你拍板，你会怎么安排这次上线？",
        "s5_dynamic_adjustment": "看到这条新信息后，你会改变刚才的安排吗？",
        "s6_integrated_plan": "综合目前所有信息，你最终会如何安排此次上线呢？",
    }.get(stage_code, "先说说你现在最直接的想法。")


def _simplify_text(text: str, stage_code: str) -> str:
    compact = re.sub(r"\s+", "", text)
    if (
        "两边" in compact
        or ("一部分参与者" in compact and "另一部分" in compact)
    ) and any(marker in compact for marker in ("进度", "返工", "质量")):
        return (
            "这里说的是两种顾虑：一边想赶进度，"
            "另一边担心返工和质量。你想先比较哪方面的影响？"
        )
    if any(marker in compact for marker in ("影响谁", "谁会", "谁的工作")):
        return "我想了解的是：这个安排一变，除了你，还有谁需要调整工作？"
    if "延迟" in compact:
        return "我想了解的是：要查延迟原因，你会先看任务进度还是交接记录？"
    question = next(
        (
            item.strip()
            for item in reversed(re.split(r"[。；;！!]", text))
            if "？" in item or "?" in item
        ),
        "",
    )
    if question:
        return f"我想了解的是：{question[:56]}"
    return _plain_question(stage_code)


def _normalize(text: str) -> str:
    return re.sub(r"[\s。！!，,？?；;：:\"'“”‘’]", "", text.strip()).lower()


__all__ = [
    "HumanisticAuthorityRequest",
    "UserTurnIntent",
    "analyze_humanistic_authority_request",
    "analyze_user_turn",
    "build_clarification_response",
    "build_guidance_response",
    "build_missing_evidence_question",
    "build_redirect_response",
    "build_stage_incomplete_prompt",
    "build_term_explanation",
    "classify_user_turn",
    "current_stage_user_turns",
    "evidence_coverage",
    "has_substantive_stage_answer",
    "is_scoring_analysis",
    "is_stage_skip_request",
    "missing_evidence",
    "validate_resolved_evidence",
    "apply_model_resolution_to_context",
]

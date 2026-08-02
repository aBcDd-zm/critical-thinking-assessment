from __future__ import annotations

import re
from dataclasses import dataclass

from app.agents.report_prompts import REPORT_DISCLAIMER
from app.agents.schemas import (
    AgentRuntimeContext,
    DimensionReport,
    DimensionScore,
    EvidenceItem,
    ReportOutput,
    ScoringOutput,
)
from app.agents.user_turn_intent import (
    classify_user_turn,
    is_scoring_analysis,
)


LOW_INFORMATION_ANSWERS = {
    "",
    "无",
    "没有",
    "不知道",
    "不清楚",
    "随便",
    "没有方案",
    "暂无",
    "没想法",
    "none",
    "no",
    "nothing",
}

DIMENSION_FEATURE_PATTERNS: dict[
    str,
    dict[str, tuple[re.Pattern[str], ...]],
] = {
    "problem_definition": {
        "core_problem": (
            re.compile(r"(?:核心|本质|真正|关键)(?:问题|矛盾|冲突)?"),
            re.compile(r"(?:不是简单|需要界定|待决策)"),
            re.compile(
                r"(?:先|首先|最先).{0,16}(?:看|判断|确认|弄清).{0,20}"
                r"(?:能不能|够不够|是不是|是否|最重要)"
            ),
        ),
        "tension": (
            re.compile(r"(?:冲突|矛盾|取舍|权衡|两难)"),
        ),
        "boundary": (
            re.compile(
                r"(?:边界|范围|限定|仅限|不包括|什么情况下|重要部分|关键部分)"
            ),
        ),
        "constraints": (
            re.compile(
                r"(?:约束|限制|窗口|预算|资源|合规|时间压力|能力上限|"
                r"五天|两人|两个人|人手|来不及|任务太多)"
            ),
        ),
        "stakeholders": (
            re.compile(
                r"(?:用户|研发|运营|市场|团队|管理层|买方|卖方|平台|相关方)"
            ),
        ),
        "hierarchy": (
            re.compile(r"(?:优先|首要|其次|层级|先.{0,20}再|主次)"),
        ),
    },
    "evidence_evaluation": {
        "data_reference": (
            re.compile(
                r"(?:数据|数量|用户反馈|指标|失败率|成功率|\d+次|\d+\s*[%％])"
            ),
        ),
        "evaluation_criterion": (
            re.compile(r"(?:比较多|不多|较多|较少|高于|低于|集中在|严重程度)"),
        ),
        "source": (
            re.compile(r"(?:来源|原始|一手|谁记录|日志|记录|出处|权威)"),
        ),
        "sample": (
            re.compile(r"(?:样本|抽样|覆盖范围|代表性|任务规模|团队配置)"),
        ),
        "quality": (
            re.compile(r"(?:可靠|真实性|准确|口径|补录|缺失|数据质量)"),
        ),
        "relevance": (
            re.compile(r"(?:相关性|可比|同类|对应|严重程度|业务影响)"),
        ),
        "cross_validation": (
            re.compile(
                r"(?:交叉验证|核对|对照|相互印证|复现|验证|抽查|让别人看|"
                r"让老师看|重新检查)"
            ),
        ),
        "limitation": (
            re.compile(
                r"(?:局限|不确定|偏差|噪声|无法排除|不能说明|可能失真|"
                r"不一定|不能马上说|不能直接说|还不能确定)"
            ),
        ),
    },
    "reasoning_argumentation": {
        "condition": (
            re.compile(r"(?:如果|若|一旦|前提|条件)"),
        ),
        "evidence_to_conclusion": (
            re.compile(r"(?:因为|依据|所以|因此|说明|意味着|据此)"),
        ),
        "assumption": (
            re.compile(r"(?:假设|前提|成立条件)"),
        ),
        "alternative": (
            re.compile(r"(?:反例|否则|相反|另一种|替代解释|也可能)"),
        ),
        "validation": (
            re.compile(r"(?:验证|复测|核实|测试|观察|检查)"),
        ),
        "causal_chain": (
            re.compile(r"(?:导致|原因|因果|推导|链路|进而)"),
        ),
    },
    "multiple_perspectives": {
        "needs": (
            re.compile(r"(?:诉求|希望|想要|想赶|关心|关注|目标|担忧|担心|怕出错)"),
        ),
        "tradeoff": (
            re.compile(r"(?:冲突|取舍|权衡|优先|兼顾)"),
            re.compile(
                r"(?:想|希望).{0,12}(?:赶|快|进度).{0,32}(?:担心|怕)"
            ),
        ),
        "risk_distribution": (
            re.compile(r"(?:承担|分担|影响|风险|成本|收益|担心|怕出错)"),
        ),
        "time_horizon": (
            re.compile(r"(?:短期|长期|当前|未来|持续影响)"),
        ),
        "mitigation": (
            re.compile(
                r"(?:沟通|协调|透明|告知|补偿|缓解|灰度|保护|一起商量|一起问)"
            ),
        ),
    },
    "integrative_decision": {
        "concrete_plan": (
            re.compile(
                r"(?:方案|计划|执行|安排|落地|灰度|上线|发布|小范围试用|"
                r"逐项检查|逐项复核)"
            ),
        ),
        "priority": (
            re.compile(
                r"(?:优先|首要|先.{0,24}再|顺序|分阶段|关键内容|必做|重点内容)"
            ),
        ),
        "responsibility": (
            re.compile(
                r"(?:负责|责任人|分工|一人|另一人|牵头|组长.{0,12}(?:记录|检查)|"
                r"老师.{0,12}(?:抽查|检查))"
            ),
        ),
        "timeline": (
            re.compile(r"(?:每天|中午|下班|第[一二三四五六七八九十\\d]+天|小时|截止|时间表|周期)"),
        ),
        "trigger": (
            re.compile(r"(?:如果|若|一旦|超过|低于|达到|阈值|门槛)"),
        ),
        "risk_control": (
            re.compile(
                r"(?:风险控制|监控|监测|限制范围|复核|校验|错误率|抽查|检查)"
            ),
        ),
        "fallback": (
            re.compile(r"(?:回滚|回退|暂停|停止|缩减|备选|兜底|降级|不确定性)"),
        ),
        "evidence_link": (
            re.compile(r"(?:依据|数据|证据|记录|日志|验证结果)"),
        ),
    },
    "dynamic_adjustment": {
        "new_information": (
            re.compile(
                r"(?:新信息|新数据|新增|变化|更新|反馈|验证结果|反而|"
                r"现在发现|\d+\s*[%％].{0,12}比.{0,12}\d+\s*[%％])"
            ),
            re.compile(
                r"(?:错误率|返工率|完成率)?.{0,8}从"
                r"\d+(?:\.\d+)?\s*[%％].{0,8}(?:升|降|变)到"
                r"\d+(?:\.\d+)?\s*[%％]"
            ),
        ),
        "adjustment": (
            re.compile(
                r"(?:调整|扩大|缩小|缩减|暂停|停止|保留|改为|回退|回滚|"
                r"先停|停下来|改回|重新检查|不再)"
            ),
        ),
        "rationale": (
            re.compile(r"(?:因为|依据|说明|显示|意味着|所以|如果|若.{0,12}可比)"),
        ),
        "threshold": (
            re.compile(
                r"(?:如果|若|一旦).{0,24}\d+(?:\.\d+)?\s*(?:[%％]|个百分点|成)"
            ),
            re.compile(r"(?:阈值|门槛|超过|低于|达到)"),
        ),
        "monitoring": (
            re.compile(
                r"(?:监控|监测|观察|跟踪|复测|复查|验证|每天看|继续看|"
                r"检查结果|重新检查|再检查)"
            ),
        ),
        "boundary": (
            re.compile(r"(?:仍然|保留|边界|除非|只在|可比|保持不变)"),
        ),
        "rollback": (
            re.compile(r"(?:回退|回滚|停止|暂停|恢复|缩减)"),
        ),
    },
}

PERSPECTIVE_ROLE_PATTERNS: dict[str, tuple[str, ...]] = {
    "user": ("用户", "买方", "买家", "卖方", "卖家"),
    "engineering": ("研发", "技术", "开发"),
    "operations": ("运营", "客服"),
    "market": ("市场", "销售"),
    "management": ("管理层", "负责人"),
    "community": ("社团", "社区", "志愿者"),
    "students": ("同学", "组员", "组长", "小组"),
    "teacher": ("老师", "教师"),
    "audience": ("观众", "听众", "看展示的人"),
}

DIMENSION_KEYWORDS: dict[str, list[str]] = {
    dimension_key: sorted(
        {
            token
            for patterns in feature_patterns.values()
            for pattern in patterns
            for token in re.findall(r"[\u4e00-\u9fff]{2,}", pattern.pattern)
        }
    )
    for dimension_key, feature_patterns in DIMENSION_FEATURE_PATTERNS.items()
}

# Kept explicit for fixture-level evidence checks and human-readable diagnostics.
DIMENSION_KEYWORDS.update(
    {
        "problem_definition": [
            "核心", "问题", "边界", "取舍", "冲突", "约束", "范围", "优先",
        ],
        "evidence_evaluation": [
            "数据", "数量", "用户反馈", "来源", "样本", "可靠", "口径", "验证",
            "核对", "偏差",
        ],
        "reasoning_argumentation": [
            "如果", "因为", "依据", "所以", "前提", "否则", "验证", "导致",
        ],
        "multiple_perspectives": [
            "研发", "运营", "市场", "用户", "团队", "诉求", "风险", "取舍",
        ],
        "integrative_decision": [
            "方案", "计划", "灰度", "回滚", "安排", "负责", "优先", "如果",
        ],
        "dynamic_adjustment": [
            "新增", "调整", "缩小", "扩大", "保留", "停止", "阈值", "监测",
        ],
    }
)

DIMENSION_LOW_SCORE_NOTES: dict[str, str] = {
    "problem_definition": (
        "本次对话尚未充分呈现对核心冲突、约束条件和决策边界的清晰界定。"
    ),
    "evidence_evaluation": (
        "本次对话尚未充分呈现对证据来源、样本代表性和可靠性的核查。"
    ),
    "reasoning_argumentation": (
        "本次对话尚未充分呈现从事实、判断到结论的完整推理链条。"
    ),
    "multiple_perspectives": (
        "本次对话尚未充分呈现对关键角色、不同立场及其影响的比较。"
    ),
    "integrative_decision": (
        "本次对话尚未充分呈现包含执行路径、风险边界和备选安排的整合方案。"
    ),
    "dynamic_adjustment": (
        "本次对话尚未充分呈现根据新信息调整方案的依据和执行边界。"
    ),
}

DIMENSION_LOW_SCORE_SUGGESTIONS: dict[str, str] = {
    "problem_definition": (
        "后续回答可进一步说明核心冲突、约束条件与决策边界。"
    ),
    "evidence_evaluation": (
        "后续回答可进一步说明证据来源、覆盖范围及可靠性。"
    ),
    "reasoning_argumentation": (
        "后续回答可进一步说明事实如何支持判断，以及结论成立的条件。"
    ),
    "multiple_perspectives": (
        "后续回答可进一步比较不同角色受到的影响和取舍依据。"
    ),
    "integrative_decision": (
        "后续回答可进一步说明执行步骤、风险边界和备选安排。"
    ),
    "dynamic_adjustment": (
        "后续回答可进一步说明调整依据和执行边界。"
    ),
}

IE_STRENGTH_NOTE = (
    "本次对话未获得该维度的公平作答机会，"
    "现有证据不足以判断该维度表现。"
)

IE_OBSERVATION_SUGGESTION = (
    "如需继续评估，可在后续相似情境中提供"
    "针对该维度的公平作答机会并继续观察。"
)

PROVISIONAL_STRENGTH_NOTE = (
    "本次对话已形成与该维度相关、可追溯的部分证据，"
    "但尚未达到支持能力评分的充分性门槛。"
)

PROVISIONAL_OBSERVATION_SUGGESTION = (
    "后续可围绕尚缺的关键行为或关系补充可核实信息，"
    "以判断该维度是否达到评分所需的证据门槛。"
)


DIMENSION_STRENGTH_NOTES: dict[str, str] = {
    "problem_definition": "能识别任务中的主要矛盾，并关注约束和优先顺序。",
    "evidence_evaluation": "能引用、比较具体信息，并关注信息是否可靠。",
    "reasoning_argumentation": "能用条件、依据和可能风险解释判断。",
    "multiple_perspectives": "能考虑不同参与者的关注点及所受影响。",
    "integrative_decision": "能形成包含优先顺序、风险控制和执行安排的方案。",
    "dynamic_adjustment": "能根据新增信息调整原计划，并提出后续检查。",
}


@dataclass(frozen=True)
class UserEvidence:
    text: str
    turn_id: int | None
    is_low_information: bool
    dimension_keys: frozenset[str]


def build_mock_scoring_output(
    context: AgentRuntimeContext,
    snapshot_type: str = "final",
) -> ScoringOutput:
    evidence_items = _collect_user_evidence(context)
    scores = [
        _score_dimension(context, dimension.dimension_key, evidence_items)
        for dimension in context.rubric_dimensions
    ]
    low_dimensions = [
        score.dimension_key
        for score in scores
        if score.score is not None and score.score <= 2
    ]
    gaps = list(low_dimensions)
    if any(score.assessment_status == "insufficient_evidence" for score in scores):
        gaps.append("部分维度缺少有效证据，暂不评分。")

    return ScoringOutput(
        snapshot_type=snapshot_type,
        summary=_build_scoring_summary(scores),
        trend_analysis="基于当前对话的确定性 mock 评分，后续可替换为真实模型评分。",
        scores=scores,
        detected_score_gaps=gaps,
        detected_argument_issues=[
            note for key, note in DIMENSION_LOW_SCORE_NOTES.items() if key in low_dimensions
        ],
    )


def build_mock_report_output(
    context: AgentRuntimeContext,
    scoring_output: ScoringOutput,
) -> ReportOutput:
    dimensions_by_key = {
        dimension.dimension_key: dimension
        for dimension in context.rubric_dimensions
    }
    evidence_items = _collect_user_evidence(context)
    observed_features = _observed_plan_features(evidence_items)
    dimension_reports = [
        _build_dimension_report(
            score,
            dimensions_by_key.get(score.dimension_key),
            observed_features,
        )
        for score in scoring_output.scores
    ]

    scored_items = [score for score in scoring_output.scores if score.score is not None]
    average_score = (
        sum(score.score for score in scored_items if score.score is not None) / len(scored_items)
        if scored_items
        else None
    )
    low_score_keys = {
        score.dimension_key
        for score in scored_items
        if score.score is not None and score.score <= 2
    }
    high_score_keys = {
        score.dimension_key
        for score in scored_items
        if score.score is not None and score.score >= 4
    }
    scored_keys = {score.dimension_key for score in scored_items}

    advantages: list[str] = []
    suggestions: list[str] = []
    development_plan: list[str] = []

    if "integrative_decision" in high_score_keys:
        advantages.append("能够提出分阶段的执行安排。")

    if "multiple_perspectives" in high_score_keys:
        advantages.append("能够兼顾不同视角，并考虑参与者的目标、顾虑和所受影响。")

    if "rollback_or_adjustment" in observed_features:
        advantages.append("对话中已提出回退或条件调整安排。")

    for dimension_key in sorted(low_score_keys):
        if dimension_key == "dynamic_adjustment":
            suggestions.append(
                _dynamic_adjustment_suggestion(observed_features)
            )
        else:
            suggestions.append(
                DIMENSION_LOW_SCORE_SUGGESTIONS.get(
                    dimension_key,
                    "后续回答可进一步提供具体、可追溯的依据。",
                )
            )

    if not scored_items:
        has_provisional = any(
            score.score_kind == "provisional"
            for score in scoring_output.scores
        )
        if has_provisional:
            suggestions.append(
                "部分维度已出现可追溯表达，但仍未达到评分所需的"
                "关键证据门槛；后续可针对未充分观察的关系继续追问。"
            )
            development_plan.append(
                "保留已有部分证据，并在后续相似任务中补充关键行为、"
                "关系和复核方式。"
            )
        else:
            suggestions.append(
                "现有对话证据不足以形成稳定判断；"
                "如需继续评估，可提供更直接的任务和观察机会。"
            )
            development_plan.append(
                "如继续测评，可围绕判断、理由和可核实证据"
                "设置更直接的问题。"
            )
    elif observed_features:
        development_plan.append(
            "保留本次对话中已经提出的具体判断或调整安排，"
            "并进一步说明其依据、复核频率和责任分工。"
        )
    else:
        development_plan.append(
            "在后续相似任务中继续记录判断依据、"
            "执行步骤和复核方式。"
        )

    return ReportOutput(
        summary=_build_report_summary(average_score, low_score_keys, high_score_keys),
        overall_level=_overall_level(average_score),
        dimension_reports=dimension_reports,
        advantages=(
            advantages
            or (["能够围绕情境任务给出初步判断。"] if scored_items else [])
        ),
        improvement_suggestions=_deduplicate(suggestions),
        development_plan=_deduplicate(development_plan),
        disclaimer=REPORT_DISCLAIMER,
    )


def _collect_user_evidence(context: AgentRuntimeContext) -> list[UserEvidence]:
    items: list[UserEvidence] = []
    for turn in context.dialogue_history:
        if turn.speaker != "user":
            continue
        text = turn.content.strip()
        analysis = turn.analysis_json or {}
        if analysis:
            if not is_scoring_analysis(analysis, text=text):
                continue
        elif classify_user_turn(text) != "substantive_answer":
            continue
        evidence_delta = analysis.get("evidence_delta") or []
        dimension_keys = frozenset(
            item.get("dimension_key")
            for item in evidence_delta
            if item.get("dimension_key")
            and item.get("extraction_confidences")
        )
        items.append(
            UserEvidence(
                text=text,
                turn_id=turn.turn_id,
                is_low_information=_is_low_information(text),
                dimension_keys=dimension_keys,
            )
        )
    return items


def _observed_plan_features(
    evidence_items: list[UserEvidence],
) -> set[str]:
    text = "\n".join(
        item.text
        for item in evidence_items
        if not item.is_low_information
    )
    features: set[str] = set()

    if re.search(r"\d+(?:\.\d+)?\s*[%％]", text) or any(
        token in text
        for token in ("阈值", "门槛", "量化条件")
    ):
        features.add("threshold")

    if any(
        token in text
        for token in (
            "监控",
            "监测",
            "持续观察",
            "继续观察",
            "观察成功率",
            "观察失败率",
            "观察错误率",
            "观察同步失败率",
        )
    ):
        features.add("monitoring")

    if any(
        token in text
        for token in (
            "重复测试",
            "连续测试",
            "复测",
            "再测试",
            "测试两轮",
            "连续两轮",
            "再跑一轮",
        )
    ):
        features.add("repeat_testing")

    if any(
        token in text
        for token in (
            "暂停扩大",
            "停止扩大",
            "暂不扩大",
            "保持小范围",
            "暂停全量",
            "暂不全量",
            "暂不上线",
        )
    ):
        features.add("pause_expansion")

    if any(
        token in text
        for token in (
            "回退",
            "回滚",
            "撤回",
            "缩小范围",
            "调整上线条件",
            "调整发布条件",
            "条件调整",
        )
    ):
        features.add("rollback_or_adjustment")

    return features


def _dynamic_adjustment_suggestion(
    observed_features: set[str],
) -> str:
    labels = {
        "threshold": "量化阈值",
        "monitoring": "监控方式",
        "repeat_testing": "重复测试安排",
        "pause_expansion": "暂停扩大条件",
        "rollback_or_adjustment": "回退或条件调整规则",
    }

    present = [
        label
        for feature, label in labels.items()
        if feature in observed_features
    ]
    missing = [
        label
        for feature, label in labels.items()
        if feature not in observed_features
    ]

    if not missing:
        return (
            "已提出量化阈值、监控、重复测试以及暂停或回退安排；"
            "可进一步说明阈值依据、监控频率和责任分工。"
        )

    if present:
        return (
            "本次对话已呈现"
            + "、".join(present)
            + "；后续可进一步说明"
            + "、".join(missing)
            + "。"
        )

    return (
        "本次对话尚未充分呈现方案调整的具体执行条件；"
        "后续可进一步说明阈值、监控、复测以及暂停或回退安排。"
    )


def _score_dimension(
    context: AgentRuntimeContext,
    dimension_key: str,
    evidence_items: list[UserEvidence],
) -> DimensionScore:
    valid_items = [
        item
        for item in evidence_items
        if not item.is_low_information
        and (not item.dimension_keys or dimension_key in item.dimension_keys)
    ]
    invalid_items = [item for item in evidence_items if item.is_low_information]
    evidence_features = [
        (item, _dimension_features(dimension_key, item.text))
        for item in valid_items
    ]
    evidence_features = [
        (item, features)
        for item, features in evidence_features
        if features
    ]
    if not evidence_features:
        return DimensionScore(
            dimension_key=dimension_key,
            score=None,
            assessment_status="insufficient_evidence",
            confidence=None,
            reason="本次对话未提供该维度的有效证据，暂不评分。",
            evidence=[],
            scoring_source="mock",
        )

    selected_items = _select_evidence_items(evidence_features)
    observed_features = set().union(
        *(features for _, features in evidence_features)
    )
    strongest_item_feature_count = max(
        len(features)
        for _, features in evidence_features
    )
    score = _score_from_features(
        dimension_key,
        observed_features,
        strongest_item_feature_count,
    )
    reason = _anchor_reason(context, dimension_key, score)
    evidence = _build_evidence(
        score,
        reason,
        selected_items,
        invalid_items,
    )
    confidence = _confidence(
        score,
        len(observed_features),
        len(selected_items),
        invalid_items,
    )

    return DimensionScore(
        dimension_key=dimension_key,
        score=score,
        assessment_status="scored",
        confidence=confidence,
        reason=reason,
        evidence=evidence,
        scoring_source="mock",
    )


def _dimension_features(
    dimension_key: str,
    text: str,
) -> set[str]:
    features = {
        feature_name
        for feature_name, patterns in DIMENSION_FEATURE_PATTERNS.get(
            dimension_key,
            {},
        ).items()
        if any(pattern.search(text) for pattern in patterns)
    }

    if dimension_key == "multiple_perspectives":
        role_count = sum(
            1
            for aliases in PERSPECTIVE_ROLE_PATTERNS.values()
            if any(alias in text for alias in aliases)
        )
        if role_count >= 2:
            features.add("multiple_roles")
        if role_count >= 4:
            features.add("broad_role_coverage")

    if (
        dimension_key == "problem_definition"
        and features == {"stakeholders"}
    ):
        return set()

    return features


def _select_evidence_items(
    evidence_features: list[tuple[UserEvidence, set[str]]],
    *,
    limit: int = 2,
) -> list[UserEvidence]:
    remaining = sorted(
        evidence_features,
        key=lambda pair: (len(pair[1]), len(pair[0].text)),
        reverse=True,
    )
    selected: list[UserEvidence] = []
    covered_features: set[str] = set()

    while remaining and len(selected) < limit:
        best_index = max(
            range(len(remaining)),
            key=lambda index: (
                len(remaining[index][1] - covered_features),
                len(remaining[index][1]),
                len(remaining[index][0].text),
            ),
        )
        item, features = remaining.pop(best_index)
        if not (features - covered_features) and selected:
            break
        selected.append(item)
        covered_features.update(features)

    return selected


def _score_from_features(
    dimension_key: str,
    observed_features: set[str],
    strongest_item_feature_count: int,
) -> int:
    feature_count = len(observed_features)
    level_five_groups: dict[str, tuple[set[str], ...]] = {
        "problem_definition": (
            {"core_problem"},
            {"boundary", "constraints"},
            {"tension", "hierarchy"},
        ),
        "evidence_evaluation": (
            {"data_reference", "evaluation_criterion"},
            {"source", "sample", "quality"},
            {"limitation"},
            {"cross_validation", "relevance"},
        ),
        "reasoning_argumentation": (
            {"condition"},
            {"evidence_to_conclusion"},
            {"assumption", "alternative"},
            {"validation", "causal_chain"},
        ),
        "multiple_perspectives": (
            {"multiple_roles"},
            {"needs"},
            {"tradeoff", "risk_distribution"},
            {"time_horizon", "mitigation"},
        ),
        "integrative_decision": (
            {"concrete_plan"},
            {"priority"},
            {"responsibility"},
            {"trigger"},
            {"risk_control", "fallback"},
        ),
        "dynamic_adjustment": (
            {"new_information"},
            {"adjustment"},
            {"rationale"},
            {"monitoring", "threshold"},
            {"boundary", "rollback"},
        ),
    }
    level_four_groups: dict[str, tuple[set[str], ...]] = {
        "problem_definition": ({"core_problem"}, {"boundary", "constraints"}),
        "evidence_evaluation": (
            {"data_reference", "evaluation_criterion"},
            {"source", "sample", "quality", "limitation"},
        ),
        "reasoning_argumentation": (
            {"evidence_to_conclusion"},
            {"condition", "assumption", "alternative"},
        ),
        "multiple_perspectives": (
            {"multiple_roles"},
            {"needs", "tradeoff", "risk_distribution"},
        ),
        "integrative_decision": ({"concrete_plan"}, {"priority", "trigger"}),
        "dynamic_adjustment": ({"new_information"}, {"adjustment"}),
    }

    def groups_satisfied(groups: tuple[set[str], ...]) -> bool:
        return all(group & observed_features for group in groups)

    if (
        feature_count >= 5
        and strongest_item_feature_count >= 3
        and groups_satisfied(level_five_groups.get(dimension_key, ()))
    ):
        return 5
    if (
        feature_count >= 4
        and strongest_item_feature_count >= 2
        and groups_satisfied(level_four_groups.get(dimension_key, ()))
    ):
        return 4
    if feature_count >= 3:
        return 3
    if feature_count >= 2:
        return 2
    return 1


def _anchor_reason(
    context: AgentRuntimeContext,
    dimension_key: str,
    score: int,
) -> str:
    anchor = next(
        (
            item
            for item in context.rubric_anchors
            if item.dimension_key == dimension_key
            and item.score_level == score
        ),
        None,
    )
    if anchor is None:
        return _score_reason(dimension_key, score)
    return f"对应“{anchor.level_name}”档标准：{anchor.behavior_desc}"


def _build_evidence(
    score: int,
    reason: str,
    selected_items: list[UserEvidence],
    invalid_items: list[UserEvidence],
) -> list[EvidenceItem]:
    evidence: list[EvidenceItem] = []
    for item in selected_items:
        evidence.append(
            EvidenceItem(
                text=item.text,
                evidence_type="supporting_evidence" if score >= 3 else "weak_evidence",
                explanation=reason,
                dialogue_turn_id=item.turn_id,
            )
        )
    for item in invalid_items[:1]:
        evidence.append(
            EvidenceItem(
                text=item.text,
                evidence_type="invalid_evidence",
                explanation="该回答信息量过低，不能作为高分证据。",
                dialogue_turn_id=item.turn_id,
            )
        )
    return evidence


def _score_reason(dimension_key: str, score: int) -> str:
    if score <= 2:
        return DIMENSION_LOW_SCORE_NOTES.get(dimension_key, "该维度证据不足。")
    return DIMENSION_STRENGTH_NOTES.get(dimension_key, "该维度已有可用证据。")


def _confidence(
    score: int,
    feature_count: int,
    evidence_count: int,
    invalid_items: list[UserEvidence],
) -> float:
    if evidence_count == 0:
        return 0.25
    if invalid_items and score <= 2:
        return 0.35
    return min(
        0.92,
        0.4
        + score * 0.07
        + min(feature_count, 6) * 0.025
        + max(0, evidence_count - 1) * 0.04,
    )


def _is_low_information(text: str) -> bool:
    return classify_user_turn(text) != "substantive_answer"


def _build_scoring_summary(
    scores: list[DimensionScore],
) -> str:
    scored = [
        score.score
        for score in scores
        if score.score is not None
    ]

    if not scored:
        return (
            "现有对话证据不足以形成可靠评分，"
            "相关维度暂不判断。"
        )

    average = sum(scored) / len(scored)

    if average < 2.5:
        return (
            "本次对话中部分维度尚未充分呈现，"
            "当前结论仅反映已经观察到的表达。"
        )

    if average < 4:
        return (
            "本次对话已呈现部分有效证据，"
            "部分观察点仍可进一步说明。"
        )

    return (
        "本次对话呈现了较完整的判断、"
        "依据和执行安排。"
    )


def _build_dimension_report(
    score: DimensionScore,
    dimension,
    observed_features: set[str],
) -> DimensionReport:
    dimension_name = (
        dimension.name
        if dimension
        else score.dimension_key
    )

    if score.score is None:
        provisional = score.score_kind == "provisional"
        evidence_quotes = list(
            dict.fromkeys(
                item.text
                for item in score.evidence
                if item.evidence_type != "invalid_evidence"
            )
        )[:2] if provisional else []
        return DimensionReport(
            dimension_key=score.dimension_key,
            dimension_name=dimension_name,
            score=None,
            assessment_status="insufficient_evidence",
            level_label="暂不评分",
            strength=(
                PROVISIONAL_STRENGTH_NOTE
                if provisional
                else IE_STRENGTH_NOTE
            ),
            weakness=None,
            evidence_quotes=evidence_quotes,
            suggestion=(
                PROVISIONAL_OBSERVATION_SUGGESTION
                if provisional
                else IE_OBSERVATION_SUGGESTION
            ),
            evidence_sufficiency_index=score.evidence_sufficiency_index,
            evidence_sufficiency_level=score.evidence_sufficiency_level,
            score_kind=score.score_kind,
            evidence_sufficiency_note=score.evidence_sufficiency_note,
        )

    low_observation = DIMENSION_LOW_SCORE_NOTES.get(
        score.dimension_key,
        "本次对话尚未充分呈现该维度的稳定表现。",
    )

    strength = (
        "本次对话已出现与该维度相关的可追溯表达。"
        if score.score <= 2
        else _score_reason(score.dimension_key, score.score)
    )

    return DimensionReport(
        dimension_key=score.dimension_key,
        dimension_name=dimension_name,
        score=score.score,
        assessment_status="scored",
        level_label=_level_label(score.score),
        strength=strength,
        weakness=(
            low_observation
            if score.score <= 3
            else None
        ),
        evidence_quotes=[
            item.text
            for item in score.evidence
            if item.evidence_type != "invalid_evidence"
        ],
        suggestion=_dimension_suggestion(
            score.dimension_key,
            score.score,
            observed_features,
        ),
        evidence_sufficiency_index=score.evidence_sufficiency_index,
        evidence_sufficiency_level=score.evidence_sufficiency_level,
        score_kind=score.score_kind,
        evidence_sufficiency_note=score.evidence_sufficiency_note,
    )


def _dimension_suggestion(
    dimension_key: str,
    score: int,
    observed_features: set[str],
) -> str:
    if dimension_key == "dynamic_adjustment":
        return _dynamic_adjustment_suggestion(observed_features)

    if score <= 2:
        return DIMENSION_LOW_SCORE_SUGGESTIONS.get(
            dimension_key,
            "后续回答可进一步提供该维度的具体、可追溯证据。",
        )

    if dimension_key == "evidence_evaluation":
        return "可进一步说明样本覆盖范围和证据可靠性。"

    return (
        "继续保留可追溯证据，"
        "并把判断进一步转化为可执行动作。"
    )


def _build_report_summary(
    average_score: float | None,
    low_score_keys: set[str],
    high_score_keys: set[str],
) -> str:
    if average_score is None:
        return (
            "现有对话证据不足以形成可靠能力判断，"
            "相关维度暂不评分。"
        )

    if low_score_keys:
        return (
            "本次对话已经呈现部分有效表现，"
            "部分维度尚未充分呈现；"
            "结论仅限于本次任务中的可观察证据。"
        )

    if high_score_keys:
        return (
            "本次对话呈现了较清晰的判断依据"
            "和执行思路。"
        )

    return (
        "本次对话具备一定分析基础，"
        "后续可继续细化判断依据和执行条件。"
    )


def _overall_level(average_score: float | None) -> str:
    if average_score is None:
        return "证据不足"
    if average_score < 1.5:
        return "明显不足"
    if average_score < 2.5:
        return "基础"
    if average_score < 3.5:
        return "中等"
    if average_score < 4.5:
        return "较强"
    return "突出"


def _level_label(score: int) -> str:
    if score == 1:
        return "明显不足"
    if score == 2:
        return "基础"
    if score == 3:
        return "中等"
    if score == 4:
        return "较强"
    return "突出"


def _deduplicate(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result

from __future__ import annotations

import re
from dataclasses import dataclass


_CJK_CHARACTER_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_CLAUSE_BOUNDARY_RE = re.compile(r"[，,；;。.!！？?\n]")


@dataclass(frozen=True)
class BehaviorEvidenceSpan:
    quote: str
    start: int
    end: int
    matched_patterns: tuple[str, ...]


def _matches(text: str, patterns: tuple[str, ...]) -> list[str]:
    return [
        pattern
        for pattern in patterns
        if re.search(pattern, text, flags=re.IGNORECASE)
    ]


def extract_behavior_signals(
    text: str,
    *,
    allow_dynamic: bool,
) -> dict[str, dict[str, list[str]]]:
    """Extract plain-language evidence signals for the deterministic fallback.

    The rules intentionally recognize relationships in ordinary school-age
    language instead of requiring adult product-management vocabulary.
    Dynamic-adjustment evidence is gated until real counter-evidence has been
    released, so hypothetical "if" statements cannot be scored as updating.
    """

    compact = re.sub(r"\s+", "", text)
    rules: dict[str, dict[str, tuple[str, ...]]] = {
        "problem_definition": {
            "distinguish_surface_and_decision": (
                r"(?:核心|表面|本质|不是简单|最先判断)",
                r"(?:先|首先|最先).{0,12}(?:看|判断|确认|弄清|分清).{0,20}"
                r"(?:能不能|够不够|是不是|是否|真正|最重要|问题|原因)",
                r"(?:真正|核心|本质|最重要)(?:的)?(?:问题|部分|事情|原因)",
                r"(?:不能|不要)(?:只|马上).{0,12}(?:看|认为|决定)",
            ),
            "define_boundaries_constraints_stakeholders": (
                r"(?:边界|约束|范围|限制|相关方|受影响)",
                r"(?:五天|两人|两个人|时间|期限|来不及|资源|人手|任务太多|"
                r"工作太多|范围|重要部分|受影响|会影响)",
                r"(?:只|先)(?:做|保留|完成).{0,12}(?:重要|关键|一部分)",
            ),
            "frame_discussable_decision_problem": (
                r"(?:需要判断|决定|决策|是否|权衡|取舍)",
                r"(?:能不能|够不够|是不是|是否).{0,24}(?:完成|做完|出错|可行|继续)",
                r"(?:哪些|哪一部分).{0,16}(?:先做|保留|删掉|检查)",
                r"(?:进度|赶时间).{0,12}(?:质量|出错|返工)",
                r"(?:决定|取舍|权衡|选择|简化|保留)",
            ),
        },
        "evidence_evaluation": {
            "inspect_source_sample_quality": (
                r"(?:来源|原始|样本|日志|记录|口径|可比)",
                r"(?:那|这)?\d+次.{0,20}(?:另|也|其中|没有|是不是|差不多)",
                r"(?:抽查|核对|查记录|看记录|问清|问老师|让.{0,8}(?:看|检查))",
                r"(?:来源|样本|日志|记录|谁说的|谁统计|怎么统计|是否一样|差不多)",
            ),
            "distinguish_fact_opinion_assumption": (
                r"(?:事实|判断|观点|推测|假设)",
                r"(?:不能|不该)(?:马上|直接|因为).{0,24}(?:说|认为|说明|肯定|一定)",
                r"(?:可能|也许).{0,20}(?:但|不过|不一定|不能)",
                r"(?:不一定|只是猜|还不能确定|不能说明|不能证明)",
                r"(?:事实|观点|意见|推测|假设)",
            ),
            "identify_gap_and_verification": (
                r"(?:核实|验证|交叉|复核|盲核|不确定|不可比)",
                r"(?:还想|还要|需要|先要).{0,8}(?:知道|问|看|查|确认|核实|检查)",
                r"(?:看|查|问|确认|核实).{0,16}(?:有没有|是不是|能不能|为什么|原因)",
                r"(?:抽查|复核|核对|让别人看|让老师看|重新检查|再检查)",
            ),
        },
        "reasoning_argumentation": {
            "explain_premise_evidence_inference": (
                r"(?:前提|依据|因为|所以|因此|意味着)",
                r"(?:依赖|基于|根据).{0,24}(?:前提|依据|证据|条件)",
                r"(?:因为|依据|根据).{0,40}(?:所以|因此|说明|我会|我觉得)",
                r"(?:所以|因此|说明|意味着).{0,36}",
                r"\d+(?:\.\d+)?\s*[%％次]?.{0,30}(?:所以|说明|比|不能)",
            ),
            "identify_assumption_risk_counterexample": (
                r"(?:假设|风险|反例|否则|证伪|推翻|不成立)",
                r"(?:如果|要是|否则|不然|反过来|也可能|但也|不过).{0,40}",
                r"(?:风险|反例|假设|不成立|出错|返工)",
            ),
            "connect_evidence_and_conclusion": (
                r"(?:证据|依据).{0,32}(?:结论|支持|说明|判断|继续|进入)",
                r"(?:支持|削弱|推翻).{0,24}(?:结论|判断|继续|扩大)",
                r"(?:因为|依据|根据).{0,50}(?:所以|因此|说明|判断)",
                r"(?:所以|因此|说明|意味着).{0,40}(?:会|要|不能|可以|应该)",
                r"\d+(?:\.\d+)?\s*[%％次]?.{0,24}(?:比|说明|不能马上说|不一定)",
            ),
        },
        "multiple_perspectives": {
            "identify_multiple_stakeholders": (
                r"(?:参与者|复核人|下游|负责人|用户|团队|研发|运营|市场|客服)",
                r"(?:同学|组员|组长|老师|负责人|观众|听众|使用的人|用户|团队|"
                r"研发|运营|市场|客服).{0,40}(?:同学|组员|组长|老师|负责人|"
                r"观众|听众|使用的人|用户|团队|研发|运营|市场|客服)",
                r"(?:一方|一组|有人).{0,32}(?:另一方|另一组|另一些人)",
            ),
            "compare_goals_risks_benefits": (
                r"(?:关心|承担|风险|成本|收益|取舍|权衡|优先)",
                r"(?:想|希望).{0,12}(?:赶|快|进度).{0,40}(?:担心|怕).{0,12}(?:错|质量|返工)",
                r"(?:一方|一组|有人).{0,24}(?:但|而|另一方|另一组).{0,30}",
                r"(?:不同|各自).{0,12}(?:想法|目标|担心|风险|好处)",
                r"(?:关心|担心|影响|风险|成本|收益|取舍|权衡)",
            ),
            "analyze_short_long_term_conflict": (
                r"(?:短期|长期|期限|后续|不把风险转嫁|优先原则)",
                r"(?:先|短期|眼前).{0,24}(?:以后|后面|长期|后续)",
                r"(?:不能|不要).{0,20}(?:把风险|只顾|只看)",
                r"(?:优先|一起商量|一起问|协调|谁负责|怎么分工)",
            ),
        },
        "integrative_decision": {
            "define_plan_priority_conditions": (
                r"(?:方案|安排|优先|第一步|如果|条件|分阶段)",
                r"(?:先|第一步|首先).{0,28}(?:再|然后|接着|第二步)",
                r"(?:优先|最重要|先做|先检查|先完成)",
                r"(?:如果|要是).{0,32}(?:就|再|否则|停止|继续)",
                r"(?:方案|计划|安排|分阶段|保留关键)",
            ),
            "explain_tradeoff_risk_fallback": (
                r"(?:取舍|风险|回滚|回退|暂停|备选|兜底)",
                r"(?:如果|要是).{0,32}(?:停止|暂停|恢复|改回|重新|不再)",
                r"(?:风险|出错|返工).{0,28}(?:停止|检查|保留|改回)",
                r"(?:备选|兜底|回退|回滚|取舍|权衡|暂停)",
            ),
            "convert_judgment_to_actions": (
                r"(?:负责|分工|每天|第一天|第二天|第三天|监测|复盘|抽查)",
                r"(?:谁|同学|组员|老师|负责人|一人|另一人).{0,20}(?:做|负责|检查|问)",
                r"(?:每天|第一天|第二天|第三天|五天|中午|放学前).{0,28}",
                r"(?:分工|安排|检查|抽查|记录|复核|重新做|重新检查)",
            ),
        },
    }

    if allow_dynamic:
        rules["dynamic_adjustment"] = {
            "update_or_retain_judgment_with_reason": (
                r"(?:调整|扩大|缩小|保留|推翻|回滚)",
                r"(?:18|返工|错误|问题).{0,30}(?:所以|说明).{0,24}"
                r"(?:停|改|恢复|重新|不再|调整)",
                r"(?:本来|原来|原先).{0,30}(?:现在|但现在|新情况).{0,30}"
                r"(?:改|停|保留|恢复|重新)",
                r"(?:因为|根据|看到).{0,30}(?:新|18|返工|错误|问题).{0,30}"
                r"(?:调整|停止|改为|保留|恢复)",
            ),
            "explain_new_information_impact": (
                r"(?:新信息|新数据|试用|等待时间|返工|不变)",
                r"(?:18|返工|错误|问题).{0,32}(?:比|多|高|说明|影响).{0,32}"
                r"(?:停|改|恢复|重新|检查|原安排)",
                r"(?:新信息|新数据|新情况|现在发现).{0,32}"
                r"(?:改变|调整|不变|保留|停止)",
                r"(?:不再|先停|停下来|恢复原来|改回|重新检查)",
            ),
            "propose_followup_validation_adjustment": (
                r"(?:监测|验证|阈值|超过|低于|停止|连续)",
                r"(?:再|重新|继续|每天).{0,16}(?:检查|看|验证|测试|记录|观察)",
                r"(?:找出|查清|确认).{0,24}(?:原因|问题|哪里).{0,24}"
                r"(?:再|然后|才)",
                r"(?:如果|要是).{0,30}(?:恢复|继续|停止|再改|重新)",
                r"(?:阈值|门槛|连续|超过|低于)",
            ),
        }

    signals: dict[str, dict[str, list[str]]] = {}
    for dimension_key, behaviors in rules.items():
        for behavior_key, patterns in behaviors.items():
            matched = _matches(compact, patterns)
            if matched:
                signals.setdefault(dimension_key, {})[behavior_key] = matched
    return signals


def extract_behavior_evidence_spans(
    text: str,
    *,
    allow_dynamic: bool,
) -> dict[str, dict[str, BehaviorEvidenceSpan]]:
    """Return the smallest exact user span supporting each detected behavior.

    Matching still uses the frozen behavior regexes. The span is mapped back to
    the untouched source text and expanded only within its clause when a short
    keyword match contains fewer than four Chinese characters.
    """

    signals = extract_behavior_signals(text, allow_dynamic=allow_dynamic)
    compact, source_offsets = _compact_text_with_offsets(text)
    evidence: dict[str, dict[str, BehaviorEvidenceSpan]] = {}
    for dimension_key, behaviors in signals.items():
        for behavior_key, patterns in behaviors.items():
            candidates: list[tuple[int, int, str]] = []
            for pattern in patterns:
                for match in re.finditer(pattern, compact, flags=re.IGNORECASE):
                    if match.start() == match.end() or not source_offsets:
                        continue
                    source_start = source_offsets[match.start()]
                    source_end = source_offsets[match.end() - 1] + 1
                    expanded = _minimum_source_span(
                        text,
                        source_start,
                        source_end,
                        minimum_chinese_characters=4,
                    )
                    if expanded is not None:
                        candidates.append((*expanded, pattern))
            if not candidates:
                continue
            source_start, source_end, _ = min(
                candidates,
                key=lambda item: (
                    item[1] - item[0],
                    item[0],
                    item[2],
                ),
            )
            evidence.setdefault(dimension_key, {})[behavior_key] = (
                BehaviorEvidenceSpan(
                    quote=text[source_start:source_end],
                    start=source_start,
                    end=source_end,
                    matched_patterns=tuple(patterns),
                )
            )
    return evidence


def _compact_text_with_offsets(text: str) -> tuple[str, list[int]]:
    characters: list[str] = []
    offsets: list[int] = []
    for index, character in enumerate(text):
        if character.isspace():
            continue
        characters.append(character)
        offsets.append(index)
    return "".join(characters), offsets


def _minimum_source_span(
    text: str,
    match_start: int,
    match_end: int,
    *,
    minimum_chinese_characters: int,
) -> tuple[int, int] | None:
    raw_match = text[match_start:match_end]
    if len(_CJK_CHARACTER_RE.findall(raw_match)) >= minimum_chinese_characters:
        return _trim_whitespace(text, match_start, match_end)

    clause_start = 0
    for boundary in _CLAUSE_BOUNDARY_RE.finditer(text, 0, match_start):
        clause_start = boundary.end()
    next_boundary = _CLAUSE_BOUNDARY_RE.search(text, match_end)
    clause_end = next_boundary.start() if next_boundary else len(text)
    chinese_positions = [
        index
        for index in range(clause_start, clause_end)
        if _CJK_CHARACTER_RE.fullmatch(text[index])
    ]
    if len(chinese_positions) < minimum_chinese_characters:
        return None

    windows: list[tuple[int, int]] = []
    width = minimum_chinese_characters
    for index in range(len(chinese_positions) - width + 1):
        window = chinese_positions[index : index + width]
        candidate_start = min(match_start, window[0])
        candidate_end = max(match_end, window[-1] + 1)
        if candidate_start < clause_start or candidate_end > clause_end:
            continue
        windows.append(
            _trim_whitespace(text, candidate_start, candidate_end)
        )
    if not windows:
        return None
    return min(
        windows,
        key=lambda item: (
            item[1] - item[0],
            abs(item[0] - match_start) + abs(item[1] - match_end),
            item[0],
        ),
    )


def _trim_whitespace(
    text: str,
    start: int,
    end: int,
) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


__all__ = [
    "BehaviorEvidenceSpan",
    "extract_behavior_evidence_spans",
    "extract_behavior_signals",
]

# DEV-C Scoring And Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the full-stack C scoring/report backend line so a completed assessment session can produce schema-valid six-dimension scoring, traceable evidence, a structured user report, persisted database records, and validation scripts for one final PR.

**Architecture:** Implement C-owned Agent modules first, then persistence services, then validation scripts. The scoring agent produces `ScoringOutput`; the report agent consumes `ScoringOutput` and produces `ReportOutput`; services persist those outputs into existing `agent_trace`, `score_snapshot`, `score_result`, `score_evidence`, and `assessment_report` tables without adding migrations.

**Tech Stack:** FastAPI service layer, SQLAlchemy ORM, Pydantic v2 models in `backend/app/agents/schemas.py`, MySQL 8 through existing Docker Compose, PowerShell validation commands on Windows.

## Global Constraints

- Work on the existing `Dev-ryx` branch unless a reviewer asks for a feature branch.
- Use one final PR with multiple commits named by task, for example `DEV-C-001 implement scoring agent baseline`.
- Do not edit B-owned files: `backend/app/agents/dialogue_policy.py`, `backend/app/agents/followup_agent.py`, `backend/app/agents/host_agent.py`, `backend/app/agents/mock_dialogue.py`, `backend/app/agents/dialogue_llm_client.py`, `backend/app/agents/dialogue_prompts.py`, `backend/app/agents/dialogue_text_streamer.py`.
- Do not edit `backend/app/models/**` or `backend/migrations/**`.
- Do not edit `backend/app/services/session_service.py` in this plan; final automatic session integration belongs to full-stack A unless explicitly authorized.
- If `backend/app/agents/schemas.py` must change, stop and align with full-stack A and B first. This plan assumes no schema change is required.
- Evidence text must come from `AgentRuntimeContext.dialogue_history`; generated text cannot be used as user evidence.
- Low-information answers such as `不知道`, `无`, `没有方案`, `不清楚`, and blank-like answers must produce low score, low confidence, invalid evidence, or score gaps.
- Report copy must avoid clinical diagnosis, personality labeling, recruitment/selection conclusions, or high-risk decision language.
- The final verification commands are:
  - `.\.venv\Scripts\python.exe scripts\check_agent_contract.py`
  - `.\.venv\Scripts\python.exe scripts\check_scoring_report_agent.py`
  - `.\.venv\Scripts\python.exe scripts\check_report_generation_flow.py`

---

## File Structure

- Create `backend/app/agents/rag_context.py`: Builds fixed professional context from runtime rubric dimensions and anchors. It must not query the database directly.
- Create `backend/app/agents/scoring_prompts.py`: Holds prompt assembly helpers for future real-model scoring. Mock scoring uses the same context shape.
- Create `backend/app/agents/report_prompts.py`: Holds prompt assembly helpers for future real-model reports. Mock reporting uses the same context shape.
- Create `backend/app/agents/mock_scoring_report.py`: Contains deterministic scoring/report helper functions, evidence extraction, low-information detection, score heuristics, level labels, and report text generation.
- Create `backend/app/agents/scoring_agent.py`: Public `ScoringAgent.generate(context: AgentRuntimeContext, snapshot_type: Literal["turn", "stage", "final"] = "final") -> ScoringOutput`.
- Create `backend/app/agents/report_agent.py`: Public `ReportAgent.generate(context: AgentRuntimeContext, scoring: ScoringOutput) -> ReportOutput`.
- Modify `backend/app/agents/__init__.py`: Export `ScoringAgent` and `ReportAgent`.
- Create `backend/app/services/scoring_service.py`: Persists `ScoringOutput` and its `AgentTrace`, `ScoreSnapshot`, `ScoreResult`, and `ScoreEvidence` records.
- Create `backend/app/services/report_service.py`: Persists `ReportOutput` and its `AgentTrace` / `AssessmentReport`, using update-if-exists behavior for one report per session.
- Create `backend/scripts/check_scoring_report_agent.py`: Validates low, medium, and strong fixture contexts without database writes.
- Create `backend/scripts/check_report_generation_flow.py`: Uses the real database to create or reuse a completed mock session, generate scoring/report outputs, persist them, and read the report through `SessionService.get_report`.

---

### Task 1: DEV-C-001 Scoring Agent Baseline

**Files:**
- Create: `backend/app/agents/rag_context.py`
- Create: `backend/app/agents/scoring_prompts.py`
- Create: `backend/app/agents/mock_scoring_report.py`
- Create: `backend/app/agents/scoring_agent.py`
- Modify: `backend/app/agents/__init__.py`
- Test: `backend/scripts/check_scoring_report_agent.py`

**Interfaces:**
- Consumes: `AgentRuntimeContext`, `RubricDimensionContext`, `RubricAnchorContext`, `DialogueTurnContext`, `ScoringOutput`, `DimensionScore`, `EvidenceItem`.
- Produces: `ScoringAgent.generate(context: AgentRuntimeContext, snapshot_type: Literal["turn", "stage", "final"] = "final") -> ScoringOutput`.

- [ ] **Step 1: Create the failing validation script skeleton**

Create `backend/scripts/check_scoring_report_agent.py` with one failing import first:

```python
from __future__ import annotations

from app.agents import ScoringAgent


def main() -> None:
    raise AssertionError("ScoringAgent import should exist before this script is completed")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the script to verify the current failure**

Run:

```powershell
cd D:\github\psychological-assessment-demo\backend
.\.venv\Scripts\python.exe scripts\check_scoring_report_agent.py
```

Expected: FAIL with `ImportError` or the explicit `AssertionError`.

- [ ] **Step 3: Add professional context builder**

Create `backend/app/agents/rag_context.py`:

```python
from __future__ import annotations

from app.agents.schemas import AgentRuntimeContext


def build_professional_context(context: AgentRuntimeContext) -> list[str]:
    sections: list[str] = []
    for dimension in context.rubric_dimensions:
        behaviors = "；".join(dimension.observable_behaviors) or "未配置可观察行为"
        invalid = dimension.invalid_evidence_desc or "未配置无效证据说明"
        sections.append(
            f"{dimension.dimension_key}｜{dimension.name}：{dimension.definition} "
            f"可观察行为：{behaviors}。无效证据：{invalid}。"
        )

    anchor_lines: list[str] = []
    for anchor in context.rubric_anchors:
        anchor_lines.append(
            f"{anchor.dimension_key} {anchor.score_level}分 {anchor.level_name}："
            f"{anchor.behavior_desc}"
        )
    if anchor_lines:
        sections.append("评分锚点：" + "；".join(anchor_lines))

    sections.append(
        "报告边界：本测评只解释本次情境对话中的审辩式思维表现，"
        "不得输出临床诊断、人格定性或高风险选拔结论。"
    )
    return sections
```

- [ ] **Step 4: Add scoring prompt helpers**

Create `backend/app/agents/scoring_prompts.py`:

```python
from __future__ import annotations

from app.agents.schemas import AgentRuntimeContext


SCORING_SYSTEM_PROMPT = (
    "你是审辩式思维测评评分 Agent。你只根据对话原文、rubric 和阶段目标评分。"
    "证据句必须来自对话原文，不允许编造。输出必须能映射为 ScoringOutput。"
)


def build_scoring_prompt(context: AgentRuntimeContext) -> str:
    turns = "\n".join(
        f"{turn.turn_index or '-'}｜{turn.speaker}｜{turn.content_type}｜{turn.content}"
        for turn in context.dialogue_history
    )
    dimensions = "\n".join(
        f"{dimension.dimension_key}｜{dimension.name}｜{dimension.definition}"
        for dimension in context.rubric_dimensions
    )
    return (
        f"情境：{context.scenario.title}\n"
        f"当前阶段：{context.stage.stage_code} {context.stage.title}\n"
        f"阶段目标：{context.stage.stage_goal}\n\n"
        f"维度：\n{dimensions}\n\n"
        f"对话历史：\n{turns}\n\n"
        "请输出六维评分、证据、评分缺口和论证问题。"
    )
```

- [ ] **Step 5: Add deterministic mock scoring helpers**

Create `backend/app/agents/mock_scoring_report.py` with these public functions:

```python
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.agents.schemas import (
    AgentRuntimeContext,
    DimensionReport,
    DimensionScore,
    EvidenceItem,
    ReportOutput,
    ScoringOutput,
)


LOW_INFORMATION_MARKERS = {
    "",
    "无",
    "不知道",
    "不清楚",
    "没有",
    "没有方案",
    "没方案",
    "随便",
    "都可以",
}

DIMENSION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "problem_definition": ("问题", "核心", "边界", "约束", "决策", "范围"),
    "evidence_evaluation": ("数据", "证据", "核实", "来源", "样本", "反馈", "指标"),
    "reasoning_argumentation": ("因为", "如果", "所以", "前提", "假设", "推理", "条件"),
    "multiple_perspectives": ("用户", "市场", "研发", "客服", "运营", "团队", "利益"),
    "integrative_decision": ("方案", "灰度", "延期", "上线", "回滚", "优先级", "负责人"),
    "dynamic_adjustment": ("调整", "新信息", "变化", "风险", "阈值", "监控", "更新"),
}


@dataclass(frozen=True)
class EvidenceCandidate:
    turn_id: int | None
    text: str
    evidence_type: str
    explanation: str


def is_low_information_answer(text: str) -> bool:
    normalized = "".join(text.strip().lower().split())
    return normalized in LOW_INFORMATION_MARKERS or len(normalized) <= 1


def user_turns(context: AgentRuntimeContext):
    return [turn for turn in context.dialogue_history if turn.speaker == "user"]


def extract_dimension_evidence(
    context: AgentRuntimeContext,
    dimension_key: str,
) -> list[EvidenceItem]:
    keywords = DIMENSION_KEYWORDS.get(dimension_key, ())
    candidates: list[EvidenceCandidate] = []
    invalid_seen = False

    for turn in user_turns(context):
        text = turn.content.strip()
        if is_low_information_answer(text):
            invalid_seen = True
            candidates.append(
                EvidenceCandidate(
                    turn_id=turn.turn_id,
                    text=text or "空白回答",
                    evidence_type="invalid_evidence",
                    explanation="用户未提供可用于该维度评分的具体判断或理由。",
                )
            )
            continue

        matched = [keyword for keyword in keywords if keyword in text]
        if matched:
            evidence_type = "supporting_evidence" if len(text) >= 18 else "weak_evidence"
            candidates.append(
                EvidenceCandidate(
                    turn_id=turn.turn_id,
                    text=text,
                    evidence_type=evidence_type,
                    explanation=f"回答中出现与{dimension_key}相关的表达：{', '.join(matched[:3])}。",
                )
            )

    if candidates:
        selected = candidates[:2]
        return [
            EvidenceItem(
                text=item.text,
                evidence_type=item.evidence_type,
                explanation=item.explanation,
                dialogue_turn_id=item.turn_id,
            )
            for item in selected
        ]

    if invalid_seen:
        return []
    return []


def score_dimension(context: AgentRuntimeContext, dimension_key: str) -> DimensionScore:
    dimension = next(
        item for item in context.rubric_dimensions if item.dimension_key == dimension_key
    )
    evidence = extract_dimension_evidence(context, dimension_key)
    invalid_count = sum(
        1 for item in evidence if item.evidence_type == "invalid_evidence"
    )
    supporting_count = sum(
        1 for item in evidence if item.evidence_type == "supporting_evidence"
    )
    weak_count = sum(1 for item in evidence if item.evidence_type == "weak_evidence")
    total_user_turns = len(user_turns(context))
    low_info_turns = sum(1 for turn in user_turns(context) if is_low_information_answer(turn.content))

    if total_user_turns > 0 and low_info_turns >= max(1, total_user_turns // 2):
        score = 1
        confidence = 0.35
        reason = f"{dimension.name}证据严重不足，用户多次给出低信息回答。"
    elif supporting_count >= 2:
        score = 4
        confidence = 0.78
        reason = f"{dimension.name}有多条可追溯证据支持，但仍需人工复核高分锚点。"
    elif supporting_count == 1 and weak_count >= 1:
        score = 3
        confidence = 0.68
        reason = f"{dimension.name}有一条较明确证据和一条弱证据，表现达到中等水平。"
    elif supporting_count == 1 or weak_count >= 1:
        score = 2
        confidence = 0.55
        reason = f"{dimension.name}出现有限证据，但具体性和完整性不足。"
    elif invalid_count > 0:
        score = 1
        confidence = 0.42
        reason = f"{dimension.name}主要证据为无效或低信息回答。"
    else:
        score = 1
        confidence = 0.30
        reason = f"对话中缺少可用于判断{dimension.name}的证据。"

    return DimensionScore(
        dimension_key=dimension_key,
        score=score,
        confidence=confidence,
        reason=reason,
        evidence=evidence,
        scoring_source="mock",
    )


def build_mock_scoring_output(
    context: AgentRuntimeContext,
    snapshot_type: str = "final",
) -> ScoringOutput:
    scores = [
        score_dimension(context, dimension.dimension_key)
        for dimension in context.rubric_dimensions
    ]
    gaps = [
        f"{score.dimension_key} 证据不足"
        for score in scores
        if score.score <= 2 or not score.evidence
    ]
    issues = []
    if any(score.score <= 2 for score in scores):
        issues.append("部分维度回答缺少清晰证据链或具体行动方案。")
    if any(
        evidence.evidence_type == "invalid_evidence"
        for score in scores
        for evidence in score.evidence
    ):
        issues.append("存在低信息回答，评分置信度需要下调。")

    average = sum(score.score for score in scores) / len(scores)
    if average >= 3.6:
        summary = "用户在本次对话中提供了较多可追溯证据，整体表现较完整。"
    elif average >= 2.4:
        summary = "用户提供了部分有效判断，但证据链、权衡和行动条件仍不充分。"
    else:
        summary = "用户回答整体信息量较低，本次评分证据有限。"

    return ScoringOutput(
        snapshot_type=snapshot_type,  # type: ignore[arg-type]
        summary=summary,
        trend_analysis=None,
        scores=scores,
        detected_score_gaps=gaps,
        detected_argument_issues=issues,
        fallback_used=False,
        warnings=[],
    )
```

- [ ] **Step 6: Add ScoringAgent wrapper**

Create `backend/app/agents/scoring_agent.py`:

```python
from __future__ import annotations

from typing import Literal

from app.agents.mock_scoring_report import build_mock_scoring_output
from app.agents.rag_context import build_professional_context
from app.agents.schemas import AgentRuntimeContext, ScoringOutput


class ScoringAgent:
    agent_name = "scoring"

    def generate(
        self,
        context: AgentRuntimeContext,
        snapshot_type: Literal["turn", "stage", "final"] = "final",
    ) -> ScoringOutput:
        enriched_context = context.model_copy(
            update={"professional_context": build_professional_context(context)}
        )
        return build_mock_scoring_output(enriched_context, snapshot_type=snapshot_type)
```

- [ ] **Step 7: Export ScoringAgent**

Modify `backend/app/agents/__init__.py` by adding:

```python
from app.agents.scoring_agent import ScoringAgent
```

Also add `"ScoringAgent"` to `__all__` if that file already defines `__all__`.

- [ ] **Step 8: Replace validation script with three fixture contexts**

Replace `backend/scripts/check_scoring_report_agent.py` with a script that builds low, medium, and strong `AgentRuntimeContext` objects and validates `ScoringOutput.model_validate(...)`. The script must include these assertions:

```python
assert len(output.scores) == 6
assert all(1 <= item.score <= 5 for item in output.scores)
assert all(item.reason for item in output.scores)
assert any(output.detected_score_gaps)  # low case only
assert any(
    evidence.dialogue_turn_id is not None
    for score in output.scores
    for evidence in score.evidence
)
```

The low fixture user turns must include `不知道`, `无`, and `没有方案`. The strong fixture must include concrete phrases about core problem, data source, stakeholders, gray release, rollback, and dynamic adjustment.

- [ ] **Step 9: Run agent script and contract check**

Run:

```powershell
cd D:\github\psychological-assessment-demo\backend
.\.venv\Scripts\python.exe scripts\check_scoring_report_agent.py
.\.venv\Scripts\python.exe scripts\check_agent_contract.py
```

Expected: both commands exit with code 0.

- [ ] **Step 10: Commit Task 1**

```powershell
git add backend/app/agents/rag_context.py backend/app/agents/scoring_prompts.py backend/app/agents/mock_scoring_report.py backend/app/agents/scoring_agent.py backend/app/agents/__init__.py backend/scripts/check_scoring_report_agent.py
git commit -m "DEV-C-001 implement scoring agent baseline"
```

---

### Task 2: DEV-C-002 Evidence Extraction And Invalid Evidence Hardening

**Files:**
- Modify: `backend/app/agents/mock_scoring_report.py`
- Modify: `backend/scripts/check_scoring_report_agent.py`

**Interfaces:**
- Consumes: `extract_dimension_evidence(context, dimension_key) -> list[EvidenceItem]`.
- Produces: stable evidence type values: `supporting_evidence`, `weak_evidence`, `invalid_evidence`.

- [ ] **Step 1: Extend script assertions before changing implementation**

In `backend/scripts/check_scoring_report_agent.py`, add a `validate_evidence_integrity` function:

```python
def validate_evidence_integrity(output: ScoringOutput, source_texts: set[str]) -> None:
    allowed_types = {"supporting_evidence", "weak_evidence", "invalid_evidence"}
    for score in output.scores:
        for evidence in score.evidence:
            assert evidence.evidence_type in allowed_types, evidence.evidence_type
            assert evidence.text in source_texts or evidence.text == "空白回答"
            if evidence.evidence_type != "invalid_evidence":
                assert evidence.dialogue_turn_id is not None
```

Call it for each fixture.

- [ ] **Step 2: Run script to expose evidence quality gaps**

Run:

```powershell
cd D:\github\psychological-assessment-demo\backend
.\.venv\Scripts\python.exe scripts\check_scoring_report_agent.py
```

Expected: FAIL if evidence type, source text, or turn id handling is incomplete.

- [ ] **Step 3: Harden low-information detection**

Modify `LOW_INFORMATION_MARKERS` in `mock_scoring_report.py` to include:

```python
LOW_INFORMATION_MARKERS = {
    "",
    "无",
    "不知道",
    "不清楚",
    "没有",
    "没有方案",
    "没方案",
    "随便",
    "都可以",
    "不知道怎么说",
    "没有想法",
    "没想法",
    "无法判断",
}
```

Modify `is_low_information_answer`:

```python
def is_low_information_answer(text: str) -> bool:
    normalized = "".join(text.strip().lower().split())
    if normalized in LOW_INFORMATION_MARKERS:
        return True
    return len(normalized) <= 1 or normalized.replace("。", "") in LOW_INFORMATION_MARKERS
```

- [ ] **Step 4: Ensure every dimension can record invalid evidence**

Modify `extract_dimension_evidence` so the first invalid user turn is returned as `invalid_evidence` when no dimension keyword matches:

```python
if not candidates and invalid_seen:
    first_invalid = next(
        turn for turn in user_turns(context) if is_low_information_answer(turn.content)
    )
    return [
        EvidenceItem(
            text=first_invalid.content.strip() or "空白回答",
            evidence_type="invalid_evidence",
            explanation="用户未提供具体判断、证据、理由或行动方案，不能作为高水平评分证据。",
            dialogue_turn_id=first_invalid.turn_id,
        )
    ]
```

- [ ] **Step 5: Add score gap specificity**

Modify `build_mock_scoring_output` gap generation:

```python
dimension_name_by_key = {
    dimension.dimension_key: dimension.name
    for dimension in context.rubric_dimensions
}
gaps = [
    f"{dimension_name_by_key.get(score.dimension_key, score.dimension_key)}缺少充分可追溯证据"
    for score in scores
    if score.score <= 2 or not score.evidence
]
```

- [ ] **Step 6: Run evidence checks**

Run:

```powershell
cd D:\github\psychological-assessment-demo\backend
.\.venv\Scripts\python.exe scripts\check_scoring_report_agent.py
```

Expected: PASS, with printed confirmation for low, medium, and strong scoring fixtures.

- [ ] **Step 7: Commit Task 2**

```powershell
git add backend/app/agents/mock_scoring_report.py backend/scripts/check_scoring_report_agent.py
git commit -m "DEV-C-002 add traceable evidence extraction"
```

---

### Task 3: DEV-C-003 Report Agent Baseline

**Files:**
- Create: `backend/app/agents/report_prompts.py`
- Create: `backend/app/agents/report_agent.py`
- Modify: `backend/app/agents/mock_scoring_report.py`
- Modify: `backend/app/agents/__init__.py`
- Modify: `backend/scripts/check_scoring_report_agent.py`

**Interfaces:**
- Consumes: `ReportAgent.generate(context: AgentRuntimeContext, scoring: ScoringOutput) -> ReportOutput`.
- Produces: schema-valid `ReportOutput` with six dimension reports, user-facing copy, and disclaimer.

- [ ] **Step 1: Extend script to require ReportAgent**

In `backend/scripts/check_scoring_report_agent.py`, import:

```python
from app.agents import ReportAgent
from app.agents.schemas import ReportOutput
```

After each scoring fixture, call:

```python
report = ReportAgent().generate(context, output)
ReportOutput.model_validate(report.model_dump(mode="json"))
assert report.disclaimer
assert len(report.dimension_reports) == 6
assert all(item.dimension_key for item in report.dimension_reports)
assert all(item.suggestion for item in report.dimension_reports)
```

Expected before implementation: FAIL with missing `ReportAgent`.

- [ ] **Step 2: Add report prompt helpers**

Create `backend/app/agents/report_prompts.py`:

```python
from __future__ import annotations

from app.agents.schemas import AgentRuntimeContext, ScoringOutput


REPORT_SYSTEM_PROMPT = (
    "你是审辩式思维测评报告 Agent。报告面向用户展示，必须专业、克制、可解释。"
    "不得生成临床诊断、人格定性或高风险选拔结论。"
)


def build_report_prompt(context: AgentRuntimeContext, scoring: ScoringOutput) -> str:
    score_lines = "\n".join(
        f"{score.dimension_key}: {score.score}分，置信度{score.confidence}，理由：{score.reason}"
        for score in scoring.scores
    )
    return (
        f"情境：{context.scenario.title}\n"
        f"测评状态：{context.session.status}\n"
        f"评分摘要：{scoring.summary}\n\n"
        f"维度评分：\n{score_lines}\n\n"
        "请生成结构化用户报告。"
    )
```

- [ ] **Step 3: Add report generation helpers**

Append to `backend/app/agents/mock_scoring_report.py`:

```python
LEVEL_LABELS = {
    1: "证据不足",
    2: "初步表现",
    3: "中等",
    4: "较好",
    5: "突出",
}


def dimension_name_map(context: AgentRuntimeContext) -> dict[str, str]:
    return {
        dimension.dimension_key: dimension.name
        for dimension in context.rubric_dimensions
    }


def build_dimension_report(
    context: AgentRuntimeContext,
    score: DimensionScore,
) -> DimensionReport:
    names = dimension_name_map(context)
    evidence_quotes = [
        evidence.text
        for evidence in score.evidence
        if evidence.evidence_type != "invalid_evidence"
    ]
    invalid_only = bool(score.evidence) and not evidence_quotes
    dimension_name = names.get(score.dimension_key, score.dimension_key)

    if score.score >= 4:
        strength = f"{dimension_name}表现较清晰，能提供较具体的判断依据。"
        weakness = "仍建议补充证据来源、边界条件和替代方案以提升解释力。"
    elif score.score == 3:
        strength = f"{dimension_name}已有部分有效表现。"
        weakness = "证据链、权衡过程或行动条件仍不够完整。"
    elif invalid_only:
        strength = "本维度暂未观察到充分有效表现。"
        weakness = "用户回答信息量较低，无法形成稳定评分证据。"
    else:
        strength = "本维度只有有限线索。"
        weakness = "缺少具体证据、推理过程或可执行表达。"

    return DimensionReport(
        dimension_key=score.dimension_key,
        dimension_name=dimension_name,
        score=score.score,
        level_label=LEVEL_LABELS[score.score],
        strength=strength,
        weakness=weakness,
        evidence_quotes=evidence_quotes,
        suggestion=f"后续可以围绕{dimension_name}补充具体事实、判断标准和行动条件。",
    )


def build_mock_report_output(
    context: AgentRuntimeContext,
    scoring: ScoringOutput,
) -> ReportOutput:
    reports = [build_dimension_report(context, score) for score in scoring.scores]
    average = sum(score.score for score in scoring.scores) / len(scoring.scores)
    low_confidence = any(
        score.confidence is not None and score.confidence < 0.5
        for score in scoring.scores
    )
    invalid_count = sum(
        1
        for score in scoring.scores
        for evidence in score.evidence
        if evidence.evidence_type == "invalid_evidence"
    )

    if average >= 4:
        overall_level = "较高"
    elif average >= 3:
        overall_level = "中等"
    elif average >= 2:
        overall_level = "初步"
    else:
        overall_level = "证据不足"

    summary = scoring.summary
    warnings = list(scoring.warnings)
    if low_confidence or invalid_count >= 3:
        summary += " 本次对话中低信息回答较多，结果置信度较低。"
        warnings.append("limited_evidence_low_confidence")

    advantages = [
        report.strength
        for report in reports
        if report.score >= 3 and report.strength
    ][:3]
    if not advantages:
        advantages = ["本次对话暂未形成稳定优势证据。"]

    suggestions = [
        report.suggestion
        for report in reports
        if report.score <= 3
    ][:4]
    if not suggestions:
        suggestions = ["继续保持证据、推理、视角和行动方案之间的清晰连接。"]

    return ReportOutput(
        summary=summary,
        overall_level=overall_level,
        dimension_reports=reports,
        advantages=advantages,
        improvement_suggestions=suggestions,
        development_plan=[
            "做复杂决策时，先用一句话界定核心问题和约束。",
            "提出结论前，列出至少两个证据来源并说明可靠性。",
            "形成方案时，同时写明触发条件、风险控制和回滚策略。",
        ],
        disclaimer="本报告仅基于本次情境对话中的语言表现生成，不作为临床诊断、人格判断或高风险选拔结论。",
        fallback_used=False,
        warnings=warnings,
    )
```

- [ ] **Step 4: Add ReportAgent wrapper**

Create `backend/app/agents/report_agent.py`:

```python
from __future__ import annotations

from app.agents.mock_scoring_report import build_mock_report_output
from app.agents.rag_context import build_professional_context
from app.agents.schemas import AgentRuntimeContext, ReportOutput, ScoringOutput


class ReportAgent:
    agent_name = "report"

    def generate(
        self,
        context: AgentRuntimeContext,
        scoring: ScoringOutput,
    ) -> ReportOutput:
        enriched_context = context.model_copy(
            update={"professional_context": build_professional_context(context)}
        )
        return build_mock_report_output(enriched_context, scoring)
```

- [ ] **Step 5: Export ReportAgent**

Modify `backend/app/agents/__init__.py`:

```python
from app.agents.report_agent import ReportAgent
```

Also add `"ReportAgent"` to `__all__` if that file defines `__all__`.

- [ ] **Step 6: Run report agent checks**

Run:

```powershell
cd D:\github\psychological-assessment-demo\backend
.\.venv\Scripts\python.exe scripts\check_scoring_report_agent.py
```

Expected: PASS, including report validation for low, medium, and strong fixtures.

- [ ] **Step 7: Commit Task 3**

```powershell
git add backend/app/agents/report_prompts.py backend/app/agents/report_agent.py backend/app/agents/mock_scoring_report.py backend/app/agents/__init__.py backend/scripts/check_scoring_report_agent.py
git commit -m "DEV-C-003 implement report agent baseline"
```

---

### Task 4: DEV-C-004 Scoring And Report Persistence Services

**Files:**
- Create: `backend/app/services/scoring_service.py`
- Create: `backend/app/services/report_service.py`
- Test: `backend/scripts/check_report_generation_flow.py`

**Interfaces:**
- Consumes: `persist_scoring_output(session: AssessmentSession, context: AgentRuntimeContext, scoring: ScoringOutput, stage_id: int | None = None, trigger_turn_id: int | None = None) -> ScoreSnapshot`.
- Consumes: `persist_report_output(session: AssessmentSession, context: AgentRuntimeContext, report: ReportOutput, scoring_trace_id: int | None = None) -> AssessmentReport`.
- Produces: persisted `AgentTrace`, `ScoreSnapshot`, `ScoreResult`, `ScoreEvidence`, and `AssessmentReport`.

- [ ] **Step 1: Create failing flow script skeleton**

Create `backend/scripts/check_report_generation_flow.py`:

```python
from __future__ import annotations

from app.services.scoring_service import ScoringService


def main() -> None:
    raise AssertionError("ScoringService should exist before flow validation can run")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run flow script to verify failure**

Run:

```powershell
cd D:\github\psychological-assessment-demo\backend
.\.venv\Scripts\python.exe scripts\check_report_generation_flow.py
```

Expected: FAIL with missing service or explicit assertion.

- [ ] **Step 3: Implement ScoringService**

Create `backend/app/services/scoring_service.py`:

```python
from __future__ import annotations

from decimal import Decimal
from time import perf_counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.schemas import AgentRuntimeContext, ScoringOutput
from app.models.agent import AgentTrace
from app.models.assessment import AssessmentSession
from app.models.rubric import RubricDimension
from app.models.scoring import ScoreEvidence, ScoreResult, ScoreSnapshot


class ScoringService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def persist_scoring_output(
        self,
        *,
        session: AssessmentSession,
        context: AgentRuntimeContext,
        scoring: ScoringOutput,
        stage_id: int | None = None,
        trigger_turn_id: int | None = None,
        raw_output: str | None = None,
        duration_ms: int | None = None,
    ) -> ScoreSnapshot:
        started_at = perf_counter()
        trace = AgentTrace(
            session_id=session.id,
            stage_id=stage_id or session.current_stage_id,
            trigger_turn_id=trigger_turn_id,
            agent_name="scoring",
            generation_mode="mock",
            ai_generation_weight=0,
            config_snapshot_json={
                "snapshot_type": scoring.snapshot_type,
                "dimension_count": len(scoring.scores),
            },
            input_json=context.model_dump(mode="json"),
            output_json=scoring.model_dump(mode="json"),
            raw_output=raw_output,
            status="success" if scoring.status == "ok" else "failed",
            error_code=None,
            model_name="mock",
            duration_ms=duration_ms or int((perf_counter() - started_at) * 1000),
        )
        self.db.add(trace)
        self.db.flush()

        snapshot = ScoreSnapshot(
            session_id=session.id,
            stage_id=stage_id or session.current_stage_id,
            dialogue_turn_id=trigger_turn_id,
            snapshot_type=scoring.snapshot_type,
            summary=scoring.summary,
            trend_analysis=scoring.trend_analysis,
            agent_trace_id=trace.id,
        )
        self.db.add(snapshot)
        self.db.flush()

        dimensions = self._dimension_id_by_key()
        for score in scoring.scores:
            dimension_id = dimensions.get(score.dimension_key)
            if dimension_id is None:
                raise ValueError(f"Unknown rubric dimension: {score.dimension_key}")
            result = ScoreResult(
                snapshot_id=snapshot.id,
                dimension_id=dimension_id,
                score=score.score,
                reason=score.reason,
                confidence=(
                    Decimal(str(round(score.confidence, 3)))
                    if score.confidence is not None
                    else None
                ),
                scoring_source=score.scoring_source,
            )
            self.db.add(result)
            self.db.flush()

            for evidence in score.evidence:
                self.db.add(
                    ScoreEvidence(
                        score_result_id=result.id,
                        dialogue_turn_id=evidence.dialogue_turn_id,
                        evidence_text=evidence.text,
                        evidence_type=evidence.evidence_type,
                        explanation=evidence.explanation,
                    )
                )

        self.db.flush()
        return snapshot

    def _dimension_id_by_key(self) -> dict[str, int]:
        rows = self.db.execute(
            select(RubricDimension.dimension_key, RubricDimension.id).where(
                RubricDimension.status == "active"
            )
        ).all()
        return {dimension_key: dimension_id for dimension_key, dimension_id in rows}
```

- [ ] **Step 4: Implement ReportService**

Create `backend/app/services/report_service.py`:

```python
from __future__ import annotations

from time import perf_counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.schemas import AgentRuntimeContext, ReportOutput
from app.models.agent import AgentTrace
from app.models.assessment import AssessmentSession
from app.models.report import AssessmentReport, ReportTemplate


class ReportService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def persist_report_output(
        self,
        *,
        session: AssessmentSession,
        context: AgentRuntimeContext,
        report: ReportOutput,
        raw_output: str | None = None,
        duration_ms: int | None = None,
    ) -> AssessmentReport:
        started_at = perf_counter()
        template = self._get_active_template(session.scenario_id)
        trace = AgentTrace(
            session_id=session.id,
            stage_id=session.current_stage_id,
            trigger_turn_id=None,
            agent_name="report",
            generation_mode="mock",
            ai_generation_weight=0,
            config_snapshot_json={
                "template_code": template.template_code if template else None,
                "dimension_report_count": len(report.dimension_reports),
            },
            input_json=context.model_dump(mode="json"),
            output_json=report.model_dump(mode="json"),
            raw_output=raw_output,
            status="success" if report.status == "ok" else "failed",
            error_code=None,
            model_name="mock",
            duration_ms=duration_ms or int((perf_counter() - started_at) * 1000),
        )
        self.db.add(trace)
        self.db.flush()

        existing = self.db.execute(
            select(AssessmentReport).where(AssessmentReport.session_id == session.id)
        ).scalar_one_or_none()
        report_json = report.model_dump(mode="json")
        if existing is None:
            existing = AssessmentReport(
                session_id=session.id,
                report_template_id=template.id if template else None,
                agent_trace_id=trace.id,
                report_json=report_json,
                summary=report.summary,
                status="generated",
            )
            self.db.add(existing)
        else:
            existing.report_template_id = template.id if template else existing.report_template_id
            existing.agent_trace_id = trace.id
            existing.report_json = report_json
            existing.summary = report.summary
            existing.status = "generated"

        self.db.flush()
        return existing

    def _get_active_template(self, scenario_id: int | None) -> ReportTemplate | None:
        return self.db.execute(
            select(ReportTemplate)
            .where(ReportTemplate.status == "active")
            .where(
                (ReportTemplate.scenario_id == scenario_id)
                | (ReportTemplate.scenario_id.is_(None))
            )
            .order_by(ReportTemplate.scenario_id.desc(), ReportTemplate.id)
            .limit(1)
        ).scalar_one_or_none()
```

- [ ] **Step 5: Replace flow script with database validation**

Replace `backend/scripts/check_report_generation_flow.py` with a script that:

1. Creates a test session through `SessionService.create_session`.
2. Submits six concrete user turns through `SessionService.submit_turn`.
3. Marks the session completed through `SessionService.finish_session`.
4. Rebuilds `AgentRuntimeContext` through the existing private context builder only inside the script:

```python
context = SessionService(db)._build_agent_context(session, latest_user_turn=None)
```

5. Calls:

```python
scoring = ScoringAgent().generate(context, snapshot_type="final")
snapshot = ScoringService(db).persist_scoring_output(session=session, context=context, scoring=scoring)
report = ReportAgent().generate(context, scoring)
persisted = ReportService(db).persist_report_output(session=session, context=context, report=report)
db.commit()
```

6. Reads:

```python
response = SessionService(db).get_report(session.session_uuid)
assert response.report["summary"]
assert len(response.report["dimension_reports"]) == 6
```

The script must print:

```text
Report generation flow passed: session_uuid=<uuid>, snapshot_id=<id>, report_id=<id>
```

- [ ] **Step 6: Run database flow**

Run:

```powershell
cd D:\github\psychological-assessment-demo\backend
.\.venv\Scripts\python.exe scripts\check_report_generation_flow.py
```

Expected: PASS and one generated report for the test session.

- [ ] **Step 7: Commit Task 4**

```powershell
git add backend/app/services/scoring_service.py backend/app/services/report_service.py backend/scripts/check_report_generation_flow.py
git commit -m "DEV-C-004 persist scoring and reports"
```

---

### Task 5: DEV-C-005 Full Validation And PR Readiness

**Files:**
- Modify: `backend/scripts/check_scoring_report_agent.py`
- Modify: `backend/scripts/check_report_generation_flow.py`
- No frontend files in this task.

**Interfaces:**
- Consumes: all public interfaces added in Tasks 1-4.
- Produces: final validation evidence for PR description.

- [ ] **Step 1: Add script success summaries**

At the end of `check_scoring_report_agent.py`, print:

```python
print("Scoring/report agent checks passed: cases=3, dimensions=6")
```

At the end of `check_report_generation_flow.py`, print the session UUID, snapshot ID, and report ID.

- [ ] **Step 2: Run contract validation**

Run:

```powershell
cd D:\github\psychological-assessment-demo\backend
.\.venv\Scripts\python.exe scripts\check_agent_contract.py
```

Expected: `Agent contract check passed`.

- [ ] **Step 3: Run agent validation**

Run:

```powershell
cd D:\github\psychological-assessment-demo\backend
.\.venv\Scripts\python.exe scripts\check_scoring_report_agent.py
```

Expected: `Scoring/report agent checks passed: cases=3, dimensions=6`.

- [ ] **Step 4: Run database flow validation**

Run:

```powershell
cd D:\github\psychological-assessment-demo\backend
.\.venv\Scripts\python.exe scripts\check_report_generation_flow.py
```

Expected: `Report generation flow passed`.

- [ ] **Step 5: Run existing mock assessment flow**

Run:

```powershell
cd D:\github\psychological-assessment-demo\backend
.\.venv\Scripts\python.exe scripts\check_mock_assessment_flow.py
```

Expected: command exits 0. If this existing script fails because B-owned dialogue behavior changed upstream, record the failure output in the PR known issues and do not modify B-owned files.

- [ ] **Step 6: Capture git diff summary**

Run:

```powershell
git status --short
git diff --stat
```

Expected: only C-owned files plus `backend/app/agents/__init__.py` are changed. If `frontend/package-lock.json` remains modified from local npm install, do not include it in the DEV-C PR unless the team explicitly wants dependency lock changes.

- [ ] **Step 7: Commit validation script polish**

```powershell
git add backend/scripts/check_scoring_report_agent.py backend/scripts/check_report_generation_flow.py
git commit -m "DEV-C-005 add scoring report validation scripts"
```

- [ ] **Step 8: Prepare PR description**

Use this PR description:

```markdown
## Summary

- Implemented DEV-C scoring agent baseline with six-dimension mock scoring.
- Added traceable evidence extraction and invalid evidence handling for low-information answers.
- Implemented report agent baseline with structured user-facing ReportOutput.
- Added scoring/report persistence services for score_snapshot, score_result, score_evidence, and assessment_report.
- Added validation scripts for agent-only and database-backed report generation flows.

## Test Commands

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\check_agent_contract.py
.\.venv\Scripts\python.exe scripts\check_scoring_report_agent.py
.\.venv\Scripts\python.exe scripts\check_report_generation_flow.py
.\.venv\Scripts\python.exe scripts\check_mock_assessment_flow.py
```

## Scope Boundaries

- Did not modify B-owned dialogue agent files.
- Did not add migrations or modify database models.
- Did not auto-wire report generation into SessionService; final endpoint integration remains with full-stack A unless approved.

## Known Limits

- Scoring/report generation uses deterministic mock heuristics.
- Real DeepSeek scoring/report prompts are scaffolded but not treated as successful real-model integration.
- Scores are baseline estimates for demo validation and require psychology group review before formal interpretation.
```

---

## Self-Review Checklist

- Spec coverage: DEV-C-001 through DEV-C-005 are covered by Tasks 1-5.
- File ownership: no task edits B-owned dialogue files, models, or migrations.
- Schema compatibility: plan uses existing `ScoringOutput`, `EvidenceItem`, `ReportOutput`, `DimensionReport`, and does not require schema changes.
- Persistence coverage: scoring trace, score snapshot, score result, score evidence, report trace, and assessment report are covered.
- Validation coverage: contract script, agent-only script, database-backed flow script, and existing mock flow are included.
- PR shape: one PR with five logical commits is supported.

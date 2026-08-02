import argparse
import random
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import get_sessionmaker
from app.models.agent import AgentTrace
from app.models.assessment import AssessmentSession, DialogueTurn
from app.models.feedback import SessionFeedback
from app.models.participant import Participant, ParticipantProfile
from app.models.report import AssessmentReport, ReportTemplate
from app.models.rubric import RubricDimension
from app.models.scenario import (
    Scenario,
    ScenarioStage,
    ScenarioStageDimension,
    StageDynamicInfo,
    StageInterventionRule,
)
from app.models.scoring import ScoreEvidence, ScoreResult, ScoreSnapshot

SEED_VERSION = "demo_analytics_v1"
MODEL_NAME = "deepseek-v4-pro"
RANDOM_SEED = 20260630

DIMENSION_SCORE_OFFSET = {
    "problem_definition": 0.20,
    "evidence_evaluation": 0.05,
    "reasoning_argumentation": 0.10,
    "multiple_perspectives": -0.05,
    "integrated_decision": 0.00,
    "dynamic_adjustment": -0.10,
}

QUALITY_BASE_SCORE = {
    "excellent": 4.45,
    "good": 3.95,
    "steady": 3.35,
    "risky": 2.65,
}

PROFILE_TEMPLATES = [
    ("林一诺", "在校大学生", "工商管理", "产品运营", "excellent"),
    ("周予安", "在校大学生", "信息管理", "数据分析", "good"),
    ("陈若澄", "在校大学生", "市场营销", "品牌策划", "steady"),
    ("许知远", "在职人员", "项目管理", "互联网产品", "excellent"),
    ("唐嘉禾", "在职人员", "运营管理", "企业服务", "good"),
    ("沈念青", "在校大学生", "心理学", "用户研究", "steady"),
    ("顾明珩", "在职人员", "供应链管理", "制造业", "risky"),
    ("秦沐阳", "在校大学生", "计算机", "产品研发", "good"),
    ("叶清晗", "在职人员", "人力资源", "组织发展", "steady"),
    ("宋景行", "在校大学生", "金融学", "风险控制", "excellent"),
    ("陆星野", "在职人员", "市场管理", "渠道销售", "good"),
    ("何以宁", "在校大学生", "电子商务", "增长运营", "steady"),
    ("梁思齐", "在职人员", "客户成功", "SaaS 服务", "risky"),
    ("赵闻溪", "在校大学生", "公共管理", "咨询分析", "good"),
    ("孟知夏", "在职人员", "数据产品", "商业分析", "excellent"),
    ("吴景初", "在校大学生", "国际商务", "跨境运营", "steady"),
    ("蒋承泽", "在职人员", "战略运营", "平台治理", "good"),
    ("苏云舒", "在校大学生", "社会学", "用户洞察", "excellent"),
    ("魏嘉树", "在职人员", "产品经理", "金融科技", "steady"),
    ("韩知微", "在校大学生", "会计学", "审计风控", "risky"),
    ("程以航", "在职人员", "研发管理", "质量保障", "good"),
    ("白若溪", "在校大学生", "统计学", "数据建模", "excellent"),
    ("姜临风", "在职人员", "运营管理", "本地生活", "steady"),
    ("邱安澜", "在校大学生", "管理科学", "流程优化", "good"),
    ("傅明也", "在职人员", "项目交付", "企业协作", "risky"),
    ("夏予乔", "在校大学生", "传播学", "内容策略", "steady"),
    ("钟屿白", "在职人员", "商业分析", "增长策略", "excellent"),
    ("罗思远", "在校大学生", "物流管理", "供应链分析", "good"),
]

STAGE_ANSWERS = {
    1: {
        "excellent": "我会先把问题界定为：是否在质量风险可控的前提下按期上线，而不是简单讨论要不要延期。核心边界包括关键缺陷影响范围、用户承诺、灰度策略和上线后的回滚能力。",
        "good": "我认为核心问题是上线时间和质量风险之间的取舍。需要先确认哪些缺陷会影响核心用户，再决定是否按时上线或改成灰度发布。",
        "steady": "我觉得现在主要是上线会不会出问题。需要看 bug 多不多、用户是否能接受，以及延期会不会影响业务目标。",
        "risky": "最核心就是能不能按时上线。如果老板要求时间不变，我倾向于先上线，后面再修。",
    },
    2: {
        "excellent": "我会优先核实缺陷分布、复现率、影响用户比例和数据来源。如果投诉集中在核心流程，即使数量不大也要提高权重；如果样本偏小，则需要补充日志和灰度监控。",
        "good": "我需要看缺陷是不是核心功能、有没有复现数据、影响多少用户。只看测试同学的描述还不够，最好结合线上相似版本的数据。",
        "steady": "我会让测试和产品各自给出证据，比如 bug 列表、用户反馈和上线收益，然后再判断。",
        "risky": "我主要看 bug 数量。如果数量不多，说明风险应该可以接受。",
    },
    3: {
        "excellent": "研发关注修复成本，运营关注活动承诺，客服关注投诉压力，用户关注稳定体验。我的判断会把关键利益相关方分层：核心用户体验优先，其次才是短期活动节奏。",
        "good": "我会考虑研发、运营、客服和用户的不同立场。不能只看上线 KPI，也要看用户体验和后续投诉成本。",
        "steady": "不同团队肯定有不同意见，产品要综合一下大家的看法，再找一个折中方案。",
        "risky": "我觉得最终还是业务目标最重要，其他团队可以配合解决。",
    },
    4: {
        "excellent": "我的初步方案是保留上线窗口，但只开放低风险用户灰度；同时冻结非核心功能，设定 P0 缺陷清零、监控阈值和回滚预案。如果任一条件不满足，就转为延期。",
        "good": "可以考虑灰度上线，先给一部分用户使用，同时准备回滚方案。关键 bug 必须先修完。",
        "steady": "我倾向于先修主要问题，然后看时间是否还来得及。如果来不及就延期。",
        "risky": "我会先上线，因为延期会影响活动。问题可以上线后快速修。",
    },
    5: {
        "excellent": "如果新证据显示核心用户投诉上升，我会把原方案从扩大灰度调整为暂停扩量，并重新评估上线条件。这说明原判断的风险假设被削弱，需要动态修正。",
        "good": "如果出现新的严重反馈，我会调整方案，先控制灰度范围，等问题稳定后再继续上线。",
        "steady": "有新情况就要重新开会评估，看看是不是需要延期。",
        "risky": "如果已经决定上线，我会尽量不改计划，除非问题特别严重。",
    },
    6: {
        "excellent": "最终建议是条件式灰度上线：明确准入标准、监控指标、责任人和回滚阈值。这个方案兼顾业务窗口与质量底线，也能让团队基于证据持续调整。",
        "good": "我会给出灰度上线方案，列出必须修复的问题、监控指标和应急方案，让团队知道什么时候继续、什么时候暂停。",
        "steady": "我会建议谨慎上线，并让各团队准备好应急措施。",
        "risky": "我会建议按时上线，然后安排人盯着数据，有问题再处理。",
    },
}

FOLLOWUP_BY_STAGE = {
    1: "你刚才界定了上线风险。请进一步说明：这个问题的决策边界是什么，哪些因素不应该被混在一起判断？",
    2: "如果只能优先核实三类证据，你会选择哪三类？为什么它们比其他信息更关键？",
    3: "请从至少两个利益相关方视角重新审视你的方案，看看是否会得出不同的风险判断。",
    4: "你的方案里有哪些前提假设？如果其中一个假设不成立，方案应该如何调整？",
    5: "现在出现了新的反向证据。你会保留原方案、收缩方案，还是重新定义问题？请说明触发条件。",
    6: "请把最终决策压缩成可执行方案：上线条件、监控指标、责任人和回滚阈值分别是什么？",
}

FOLLOWUP_ANSWERS = {
    "excellent": "我会把判断拆成证据、风险、行动阈值三层处理，避免用单一指标直接下结论。只要关键前提变化，就同步调整方案。",
    "good": "我会补充关键数据，再根据风险大小决定是否扩大或收缩方案。判断标准要提前说清楚。",
    "steady": "我会再收集一些信息，然后和团队确认方案是不是需要调整。",
    "risky": "我觉得可以先按原计划推进，遇到问题再快速处理。",
}

FEEDBACK_COMMENTS = {
    "excellent": "情境真实感比较强，追问能顺着我的回答继续推进，最后报告里的证据引用也比较有说服力。",
    "good": "整体对话比较自然，能感觉到系统在根据回答追问。如果报告能更突出改进建议会更好。",
    "steady": "情境可以理解，但中间有些追问稍微有压力。报告解释如果再通俗一些会更好。",
    "risky": "有些追问让我感觉像在考试，部分问题希望能给更多背景信息。",
}


def reset_runtime_data(db: Session) -> None:
    dialect = db.get_bind().dialect.name
    foreign_key_disabled = False
    try:
        if dialect == "mysql":
            db.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            foreign_key_disabled = True
        elif dialect == "sqlite":
            db.execute(text("PRAGMA foreign_keys=OFF"))
            foreign_key_disabled = True

        for model in [
            ScoreEvidence,
            ScoreResult,
            ScoreSnapshot,
            AssessmentReport,
            SessionFeedback,
            AgentTrace,
            DialogueTurn,
            ParticipantProfile,
            AssessmentSession,
            Participant,
        ]:
            db.execute(delete(model))

        db.commit()
    finally:
        if foreign_key_disabled:
            if dialect == "mysql":
                db.execute(text("SET FOREIGN_KEY_CHECKS=1"))
            elif dialect == "sqlite":
                db.execute(text("PRAGMA foreign_keys=ON"))
            db.commit()


def get_required_configuration(
    db: Session,
) -> tuple[
    Scenario,
    list[ScenarioStage],
    list[RubricDimension],
    ReportTemplate | None,
    dict[int, list[RubricDimension]],
    dict[int, list[StageInterventionRule]],
    dict[int, list[StageDynamicInfo]],
]:
    scenario = db.execute(
        select(Scenario).where(Scenario.is_default.is_(True), Scenario.status == "active")
    ).scalar_one_or_none()
    if scenario is None:
        raise RuntimeError("No active default scenario found. Run scripts/seed_db.py first.")

    stages = list(
        db.execute(
            select(ScenarioStage)
            .where(ScenarioStage.scenario_id == scenario.id, ScenarioStage.status == "active")
            .order_by(ScenarioStage.stage_order)
        ).scalars()
    )
    dimensions = list(
        db.execute(
            select(RubricDimension)
            .where(RubricDimension.status == "active")
            .order_by(RubricDimension.id)
        ).scalars()
    )
    if not stages or not dimensions:
        raise RuntimeError("Scenario stages or rubric dimensions are missing.")

    report_template = db.execute(
        select(ReportTemplate)
        .where(ReportTemplate.status == "active")
        .order_by(ReportTemplate.id)
        .limit(1)
    ).scalar_one_or_none()

    stage_dimensions: dict[int, list[RubricDimension]] = {stage.id: [] for stage in stages}
    dimension_rows = db.execute(
        select(ScenarioStageDimension.stage_id, RubricDimension)
        .join(RubricDimension, ScenarioStageDimension.dimension_id == RubricDimension.id)
        .where(ScenarioStageDimension.stage_id.in_([stage.id for stage in stages]))
    ).all()
    for stage_id, dimension in dimension_rows:
        stage_dimensions.setdefault(stage_id, []).append(dimension)
    for stage in stages:
        if not stage_dimensions[stage.id]:
            stage_dimensions[stage.id] = dimensions[:2]

    rules_by_stage = {
        stage.id: list(
            db.execute(
                select(StageInterventionRule)
                .where(
                    StageInterventionRule.stage_id == stage.id,
                    StageInterventionRule.status == "active",
                )
                .order_by(StageInterventionRule.priority, StageInterventionRule.id)
            ).scalars()
        )
        for stage in stages
    }
    dynamic_infos_by_stage = {
        stage.id: list(
            db.execute(
                select(StageDynamicInfo)
                .where(StageDynamicInfo.stage_id == stage.id, StageDynamicInfo.status == "active")
                .order_by(StageDynamicInfo.priority, StageDynamicInfo.id)
            ).scalars()
        )
        for stage in stages
    }

    return (
        scenario,
        stages,
        dimensions,
        report_template,
        stage_dimensions,
        rules_by_stage,
        dynamic_infos_by_stage,
    )


def seed_demo_data(db: Session, completed_count: int, in_progress_count: int, abandoned_count: int) -> None:
    rng = random.Random(RANDOM_SEED)
    (
        scenario,
        stages,
        dimensions,
        report_template,
        stage_dimensions,
        rules_by_stage,
        dynamic_infos_by_stage,
    ) = get_required_configuration(db)

    now = datetime.utcnow().replace(microsecond=0)
    cases = build_cases(completed_count, in_progress_count, abandoned_count)
    for index, case in enumerate(cases):
        started_at = now - timedelta(days=(len(cases) - index) // 4, hours=(index * 3) % 18)
        create_case(
            db=db,
            rng=rng,
            case=case,
            scenario=scenario,
            stages=stages,
            dimensions=dimensions,
            report_template=report_template,
            stage_dimensions=stage_dimensions,
            rules_by_stage=rules_by_stage,
            dynamic_infos_by_stage=dynamic_infos_by_stage,
            started_at=started_at,
            case_index=index,
        )
    db.commit()


def build_cases(
    completed_count: int,
    in_progress_count: int,
    abandoned_count: int,
) -> list[dict[str, Any]]:
    total = completed_count + in_progress_count + abandoned_count
    if total > len(PROFILE_TEMPLATES):
        raise ValueError(f"At most {len(PROFILE_TEMPLATES)} demo cases are available.")

    cases: list[dict[str, Any]] = []
    for index, (nickname, identity, major, direction, quality) in enumerate(PROFILE_TEMPLATES[:total]):
        if index < completed_count:
            status = "completed"
        elif index < completed_count + in_progress_count:
            status = "in_progress"
        else:
            status = "abandoned"
        cases.append(
            {
                "nickname": nickname,
                "identity_type": identity,
                "major_direction": major,
                "career_direction": direction,
                "quality": quality,
                "status": status,
            }
        )
    return cases


def create_case(
    *,
    db: Session,
    rng: random.Random,
    case: dict[str, Any],
    scenario: Scenario,
    stages: list[ScenarioStage],
    dimensions: list[RubricDimension],
    report_template: ReportTemplate | None,
    stage_dimensions: dict[int, list[RubricDimension]],
    rules_by_stage: dict[int, list[StageInterventionRule]],
    dynamic_infos_by_stage: dict[int, list[StageDynamicInfo]],
    started_at: datetime,
    case_index: int,
) -> None:
    status = case["status"]
    quality = case["quality"]
    completed = status == "completed"
    abandoned = status == "abandoned"
    planned_stage_count = len(stages) if completed else (3 if abandoned else 2 + case_index % 3)
    active_stages = stages[:planned_stage_count]
    current_stage = active_stages[-1]
    completed_at = None
    duration_seconds = None
    if completed:
        duration_seconds = int((19 + (case_index % 7) * 1.7 + random_jitter(rng, 1.1)) * 60)
        completed_at = started_at + timedelta(seconds=duration_seconds)
    elif abandoned:
        duration_seconds = int((8 + case_index % 5) * 60)

    participant = Participant(
        nickname=case["nickname"],
        age_range="18-25" if case["identity_type"] == "在校大学生" else "26-35",
        identity_type=case["identity_type"],
        education_stage="本科/研究生" if case["identity_type"] == "在校大学生" else None,
        major_direction=case["major_direction"],
        career_direction=case["career_direction"],
        industry=case["career_direction"] if case["identity_type"] == "在职人员" else None,
        work_years_range=None if case["identity_type"] == "在校大学生" else "3-5年",
        organization_role="项目成员" if case["identity_type"] == "在职人员" else None,
        self_description=f"{case['identity_type']}，关注{case['career_direction']}方向。",
        info_collect_method="ai_dialogue",
        raw_basic_info={
            "source": SEED_VERSION,
            "quality_profile": quality,
            "scenario_fit": case["career_direction"],
        },
        source="demo_seed",
        status="active",
        created_at=started_at,
        updated_at=completed_at or started_at + timedelta(minutes=12),
    )
    db.add(participant)
    db.flush()

    session = AssessmentSession(
        session_uuid=str(uuid4()),
        participant_id=participant.id,
        scenario_id=scenario.id,
        current_stage_id=current_stage.id,
        selection_mode="demo_rotation",
        selection_reason="演示数据：用于后台数据分析与计划书展示",
        status=status,
        assessment_mode="demo",
        started_at=started_at,
        completed_at=completed_at,
        total_duration_seconds=duration_seconds,
        created_at=started_at,
        updated_at=completed_at or started_at + timedelta(minutes=12 + case_index % 10),
    )
    db.add(session)
    db.flush()

    profile = ParticipantProfile(
        session_id=session.id,
        raw_background_answers={
            "nickname": case["nickname"],
            "identity_type": case["identity_type"],
            "major_direction": case["major_direction"],
            "career_direction": case["career_direction"],
        },
        ai_profile_json={
            "population_type": "student" if case["identity_type"] == "在校大学生" else "employee",
            "adaptation_summary": f"情境表达偏向{case['career_direction']}相关决策任务。",
        },
        population_type="student" if case["identity_type"] == "在校大学生" else "employee",
        adaptation_tags=[case["career_direction"], case["quality"]],
        profile_version=SEED_VERSION,
        created_at=started_at,
        updated_at=session.updated_at,
    )
    db.add(profile)

    turn_index = 1
    stage_score_records: list[dict[str, Any]] = []
    last_turn: DialogueTurn | None = None
    last_trace: AgentTrace | None = None
    for stage in active_stages:
        stage_started_at = started_at + timedelta(minutes=(stage.stage_order - 1) * 4)
        ai_question = create_turn(
            db,
            session.id,
            stage.id,
            turn_index,
            "ai",
            build_stage_question(case["nickname"], stage),
            "stage_question",
            stage_started_at,
        )
        turn_index += 1

        user_answer = create_turn(
            db,
            session.id,
            stage.id,
            turn_index,
            "user",
            STAGE_ANSWERS.get(stage.stage_order, STAGE_ANSWERS[1])[quality],
            "scenario_answer",
            stage_started_at + timedelta(seconds=45 + case_index % 20),
        )
        turn_index += 1

        followup_trace = create_followup_trace(
            db=db,
            rng=rng,
            session=session,
            stage=stage,
            trigger_turn=user_answer,
            quality=quality,
            rules=rules_by_stage.get(stage.id, []),
            dynamic_infos=dynamic_infos_by_stage.get(stage.id, []),
            created_at=user_answer.created_at + timedelta(seconds=4),
            force_failure=(case_index + stage.stage_order) % 29 == 0,
        )
        followup_text = followup_trace.output_json.get("question") if followup_trace.output_json else None
        followup_turn = create_turn(
            db,
            session.id,
            stage.id,
            turn_index,
            "ai",
            str(followup_text or FOLLOWUP_BY_STAGE.get(stage.stage_order, FOLLOWUP_BY_STAGE[1])),
            "dynamic_info_question"
            if followup_trace.selected_dynamic_info_id is not None
            else "followup_question",
            followup_trace.created_at + timedelta(seconds=1),
            source_agent_trace_id=followup_trace.id,
            dynamic_info_id=followup_trace.selected_dynamic_info_id,
            intervention_rule_id=followup_trace.selected_rule_id,
        )
        turn_index += 1

        followup_answer = create_turn(
            db,
            session.id,
            stage.id,
            turn_index,
            "user",
            FOLLOWUP_ANSWERS[quality],
            "scenario_answer",
            followup_turn.created_at + timedelta(seconds=50 + case_index % 25),
        )
        turn_index += 1

        scoring_trace = create_scoring_trace(
            db,
            rng,
            session,
            stage,
            followup_answer,
            quality,
            stage_dimensions[stage.id],
            followup_answer.created_at + timedelta(seconds=5),
        )
        snapshot = ScoreSnapshot(
            session_id=session.id,
            stage_id=stage.id,
            dialogue_turn_id=followup_answer.id,
            snapshot_type="stage",
            summary=f"{stage.title}阶段表现：{stage_summary(quality)}",
            trend_analysis=trend_text(quality),
            agent_trace_id=scoring_trace.id,
            created_at=scoring_trace.created_at + timedelta(seconds=1),
        )
        db.add(snapshot)
        db.flush()
        for dimension in stage_dimensions[stage.id]:
            score = score_for_dimension(rng, quality, dimension.dimension_key)
            stage_score_records.append(
                {"dimension": dimension, "score": score, "evidence_turn": followup_answer}
            )
            create_score_result_with_evidence(
                db,
                snapshot,
                dimension,
                score,
                followup_answer,
                quality,
            )

        last_turn = followup_answer
        last_trace = scoring_trace

    if completed and last_turn is not None:
        final_scores = create_final_score_snapshot(
            db=db,
            rng=rng,
            session=session,
            dimensions=dimensions,
            quality=quality,
            last_turn=last_turn,
            last_trace=last_trace,
            created_at=(completed_at or last_turn.created_at) - timedelta(seconds=18),
        )
        report_trace = create_report_trace(
            db,
            session,
            last_turn,
            final_scores,
            (completed_at or last_turn.created_at) - timedelta(seconds=8),
        )
        create_report(
            db=db,
            session=session,
            report_template=report_template,
            report_trace=report_trace,
            final_scores=final_scores,
            quality=quality,
            created_at=completed_at or datetime.utcnow(),
        )
        if case_index % 7 != 0:
            create_feedback(db, session, quality, case_index, completed_at or datetime.utcnow())


def create_turn(
    db: Session,
    session_id: int,
    stage_id: int | None,
    turn_index: int,
    speaker: str,
    content: str,
    content_type: str,
    created_at: datetime,
    *,
    source_agent_trace_id: int | None = None,
    dynamic_info_id: int | None = None,
    intervention_rule_id: int | None = None,
) -> DialogueTurn:
    turn = DialogueTurn(
        session_id=session_id,
        stage_id=stage_id,
        turn_index=turn_index,
        speaker=speaker,
        content=content,
        content_type=content_type,
        source_agent_trace_id=source_agent_trace_id,
        dynamic_info_id=dynamic_info_id,
        intervention_rule_id=intervention_rule_id,
        created_at=created_at,
    )
    db.add(turn)
    db.flush()
    return turn


def create_followup_trace(
    *,
    db: Session,
    rng: random.Random,
    session: AssessmentSession,
    stage: ScenarioStage,
    trigger_turn: DialogueTurn,
    quality: str,
    rules: list[StageInterventionRule],
    dynamic_infos: list[StageDynamicInfo],
    created_at: datetime,
    force_failure: bool,
) -> AgentTrace:
    selected_rule = rules[(stage.stage_order - 1) % len(rules)] if rules else None
    selected_info = (
        dynamic_infos[0]
        if dynamic_infos and stage.stage_order in {2, 5, 6} and quality != "risky"
        else None
    )
    status = "failed" if force_failure else "success"
    fallback_used = force_failure
    question = FOLLOWUP_BY_STAGE.get(stage.stage_order, FOLLOWUP_BY_STAGE[1])
    if selected_info:
        question = f"{selected_info.content} 基于这条新信息，{question}"
    if fallback_used:
        question = "请你进一步说明这个判断背后的主要证据，以及你会如何处理不确定性。"

    trace = AgentTrace(
        session_id=session.id,
        stage_id=stage.id,
        trigger_turn_id=trigger_turn.id,
        prompt_template_id=None,
        agent_name="followup",
        generation_mode=selected_rule.question_generation_mode if selected_rule else "strategy_guided",
        ai_generation_weight=selected_rule.question_ai_weight if selected_rule else 45,
        config_snapshot_json={
            "stage_code": stage.stage_code,
            "selected_rule_code": selected_rule.rule_code if selected_rule else None,
            "selected_dynamic_info_code": selected_info.info_code if selected_info else None,
            "demo_seed": SEED_VERSION,
        },
        input_json={
            "latest_user_answer": trigger_turn.content,
            "stage_goal": stage.stage_goal,
            "quality_profile": quality,
        },
        output_json={
            "question": question,
            "next_action": "ask_followup",
            "fallback_used": fallback_used,
            "reason": "演示数据：根据阶段目标、用户回答和候选策略生成追问。",
        },
        raw_output=question,
        status=status,
        error_code="DEMO_FALLBACK_USED" if fallback_used else None,
        model_name=MODEL_NAME,
        duration_ms=int(rng.uniform(820, 2600)),
        selected_dynamic_info_id=selected_info.id if selected_info else None,
        selected_rule_id=selected_rule.id if selected_rule else None,
        created_at=created_at,
    )
    db.add(trace)
    db.flush()
    return trace


def create_scoring_trace(
    db: Session,
    rng: random.Random,
    session: AssessmentSession,
    stage: ScenarioStage,
    trigger_turn: DialogueTurn,
    quality: str,
    dimensions: list[RubricDimension],
    created_at: datetime,
) -> AgentTrace:
    trace = AgentTrace(
        session_id=session.id,
        stage_id=stage.id,
        trigger_turn_id=trigger_turn.id,
        prompt_template_id=None,
        agent_name="scoring",
        generation_mode="rubric_guided",
        ai_generation_weight=35,
        config_snapshot_json={
            "stage_code": stage.stage_code,
            "dimension_keys": [dimension.dimension_key for dimension in dimensions],
            "demo_seed": SEED_VERSION,
        },
        input_json={
            "answer": trigger_turn.content,
            "stage_goal": stage.stage_goal,
            "quality_profile": quality,
        },
        output_json={
            "summary": stage_summary(quality),
            "scoring_source": "ai_demo",
        },
        raw_output=None,
        status="success",
        error_code=None,
        model_name=MODEL_NAME,
        duration_ms=int(rng.uniform(1180, 3400)),
        selected_dynamic_info_id=None,
        selected_rule_id=None,
        created_at=created_at,
    )
    db.add(trace)
    db.flush()
    return trace


def create_final_score_snapshot(
    *,
    db: Session,
    rng: random.Random,
    session: AssessmentSession,
    dimensions: list[RubricDimension],
    quality: str,
    last_turn: DialogueTurn,
    last_trace: AgentTrace | None,
    created_at: datetime,
) -> list[dict[str, Any]]:
    snapshot = ScoreSnapshot(
        session_id=session.id,
        stage_id=None,
        dialogue_turn_id=last_turn.id,
        snapshot_type="final",
        summary=f"最终综合表现：{stage_summary(quality)}",
        trend_analysis=trend_text(quality),
        agent_trace_id=last_trace.id if last_trace else None,
        created_at=created_at,
    )
    db.add(snapshot)
    db.flush()

    final_scores = []
    for dimension in dimensions:
        score = score_for_dimension(rng, quality, dimension.dimension_key)
        final_scores.append({"dimension": dimension, "score": score, "evidence_turn": last_turn})
        create_score_result_with_evidence(db, snapshot, dimension, score, last_turn, quality)
    return final_scores


def create_score_result_with_evidence(
    db: Session,
    snapshot: ScoreSnapshot,
    dimension: RubricDimension,
    score: int,
    evidence_turn: DialogueTurn,
    quality: str,
) -> None:
    result = ScoreResult(
        snapshot_id=snapshot.id,
        dimension_id=dimension.id,
        score=score,
        reason=score_reason(dimension.name, score, quality),
        confidence=Decimal("0.860") if score >= 4 else Decimal("0.780"),
        scoring_source="ai_demo",
        created_at=snapshot.created_at,
    )
    db.add(result)
    db.flush()

    evidence = ScoreEvidence(
        score_result_id=result.id,
        dialogue_turn_id=evidence_turn.id,
        evidence_text=evidence_turn.content[:450],
        evidence_type="dialogue_quote",
        explanation=f"该回答可作为“{dimension.name}”维度的行为证据。",
        created_at=snapshot.created_at,
    )
    db.add(evidence)


def create_report_trace(
    db: Session,
    session: AssessmentSession,
    trigger_turn: DialogueTurn,
    final_scores: list[dict[str, Any]],
    created_at: datetime,
) -> AgentTrace:
    trace = AgentTrace(
        session_id=session.id,
        stage_id=None,
        trigger_turn_id=trigger_turn.id,
        prompt_template_id=None,
        agent_name="report",
        generation_mode="template_guided",
        ai_generation_weight=40,
        config_snapshot_json={
            "dimension_count": len(final_scores),
            "demo_seed": SEED_VERSION,
        },
        input_json={
            "final_scores": [
                {"dimension": item["dimension"].dimension_key, "score": item["score"]}
                for item in final_scores
            ]
        },
        output_json={"status": "generated", "report_type": "structured"},
        raw_output=None,
        status="success",
        error_code=None,
        model_name=MODEL_NAME,
        duration_ms=3200,
        selected_dynamic_info_id=None,
        selected_rule_id=None,
        created_at=created_at,
    )
    db.add(trace)
    db.flush()
    return trace


def create_report(
    *,
    db: Session,
    session: AssessmentSession,
    report_template: ReportTemplate | None,
    report_trace: AgentTrace,
    final_scores: list[dict[str, Any]],
    quality: str,
    created_at: datetime,
) -> None:
    dimension_scores = [
        {
            "dimension_key": item["dimension"].dimension_key,
            "dimension_name": item["dimension"].name,
            "score": item["score"],
            "evidence": item["evidence_turn"].content[:180],
        }
        for item in final_scores
    ]
    report_json = {
        "summary": report_summary(quality),
        "dimension_scores": dimension_scores,
        "strengths": strengths_for_quality(quality),
        "improvement_suggestions": suggestions_for_quality(quality),
        "development_plan": {
            "one_week": "复盘一次真实决策案例，明确问题边界、证据来源和关键假设。",
            "one_month": "练习在新证据出现时更新判断，并记录调整理由。",
            "three_months": "形成可复用的决策检查清单，覆盖六个审辩式思维维度。",
        },
        "traceability": {
            "agent_trace_id": report_trace.id,
            "snapshot_type": "final",
            "demo_seed": SEED_VERSION,
        },
    }
    report = AssessmentReport(
        session_id=session.id,
        report_template_id=report_template.id if report_template else None,
        agent_trace_id=report_trace.id,
        report_json=report_json,
        summary=report_json["summary"],
        status="generated",
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(report)


def create_feedback(
    db: Session,
    session: AssessmentSession,
    quality: str,
    case_index: int,
    created_at: datetime,
) -> None:
    base = {
        "excellent": (5, 3, 5, 2, 5, 5),
        "good": (4, 3, 4, 3, 4, 4),
        "steady": (4, 4, 3, 3, 4, 3),
        "risky": (3, 4, 2, 4, 3, 2),
    }[quality]
    feedback = SessionFeedback(
        session_id=session.id,
        realism_score=base[0],
        difficulty_score=base[1],
        naturalness_score=base[2],
        fatigue_score=base[3],
        report_trust_score=base[4],
        overall_satisfaction_score=base[5],
        open_feedback=FEEDBACK_COMMENTS[quality],
        metadata_json={
            "source": "demo_seed",
            "case_index": case_index,
            "seed_version": SEED_VERSION,
        },
        status="active",
        created_at=created_at + timedelta(minutes=1),
        updated_at=created_at + timedelta(minutes=1),
    )
    db.add(feedback)


def score_for_dimension(rng: random.Random, quality: str, dimension_key: str) -> int:
    base = QUALITY_BASE_SCORE[quality] + DIMENSION_SCORE_OFFSET.get(dimension_key, 0)
    jitter = rng.uniform(-0.45, 0.45)
    return max(1, min(5, int(round(base + jitter))))


def random_jitter(rng: random.Random, scale: float) -> float:
    return rng.uniform(-scale, scale)


def build_stage_question(nickname: str, stage: ScenarioStage) -> str:
    return (
        f"{nickname}，我们进入“{stage.title}”。"
        f"{stage.context}\n\n请回答：{stage.main_question}"
    )


def stage_summary(quality: str) -> str:
    return {
        "excellent": "能够主动界定问题边界，结合证据、风险和新信息修正判断。",
        "good": "能够识别主要风险并提出可执行方案，证据意识较清晰。",
        "steady": "能够给出基本判断，但证据权重和动态调整仍需加强。",
        "risky": "倾向快速决策，问题界定和证据核实相对不足。",
    }[quality]


def trend_text(quality: str) -> str:
    return {
        "excellent": "表现稳定，后半程在动态调整和整合决策上进一步增强。",
        "good": "整体保持中上水平，遇到新信息后能够适度修正方案。",
        "steady": "前期能回应问题，中后期需要更多结构化推理支持。",
        "risky": "多次依赖直觉判断，面对反向证据时调整不足。",
    }[quality]


def score_reason(dimension_name: str, score: int, quality: str) -> str:
    if score >= 4:
        return f"在“{dimension_name}”上表现较好，回答中能够给出明确依据并连接到决策行动。"
    if score == 3:
        return f"在“{dimension_name}”上达到基本要求，但证据深度和边界说明仍可加强。"
    return f"在“{dimension_name}”上表现偏弱，回答较依赖结论，缺少充分证据或替代方案比较。"


def report_summary(quality: str) -> str:
    return {
        "excellent": "受测者展现出较强的审辩式思维能力，能够围绕问题边界、证据质量和动态调整形成完整决策链。",
        "good": "受测者具备较好的问题分析与方案整合能力，能够在关键追问下补充证据和风险控制思路。",
        "steady": "受测者能够完成基本决策分析，但在证据评估、多元视角和动态调整方面仍有提升空间。",
        "risky": "受测者能够快速表达决策倾向，但容易跳过证据核实和替代方案比较，需要加强结构化判断。",
    }[quality]


def strengths_for_quality(quality: str) -> list[str]:
    return {
        "excellent": ["问题边界清晰", "证据意识强", "能够根据新信息调整方案"],
        "good": ["能识别关键风险", "方案具有可执行性", "能回应多方视角"],
        "steady": ["能完成基本判断", "愿意补充信息", "能理解灰度上线等折中方案"],
        "risky": ["表达直接", "行动导向明确", "能快速给出初步选择"],
    }[quality]


def suggestions_for_quality(quality: str) -> list[str]:
    return {
        "excellent": ["继续提升量化证据使用能力", "在报告中进一步明确监控阈值"],
        "good": ["加强证据来源可信度判断", "提前定义方案调整触发条件"],
        "steady": ["练习区分事实、假设和结论", "增加多方视角下的风险比较"],
        "risky": ["先界定问题再给方案", "避免只依据时间压力作决策", "补充反向证据和回滚预案"],
    }[quality]


def print_summary(db: Session) -> None:
    counts = {}
    for name, model in [
        ("participants", Participant),
        ("sessions", AssessmentSession),
        ("dialogue_turns", DialogueTurn),
        ("agent_traces", AgentTrace),
        ("score_snapshots", ScoreSnapshot),
        ("score_results", ScoreResult),
        ("score_evidence", ScoreEvidence),
        ("reports", AssessmentReport),
        ("feedback", SessionFeedback),
    ]:
        counts[name] = db.execute(select(model)).scalars().all()
    print("Demo analytics data imported:")
    for name, rows in counts.items():
        print(f"- {name}: {len(rows)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reset runtime data and import polished demo analytics records."
    )
    parser.add_argument("--completed", type=int, default=20)
    parser.add_argument("--in-progress", type=int, default=5)
    parser.add_argument("--abandoned", type=int, default=3)
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="Append demo records without clearing runtime data first.",
    )
    args = parser.parse_args()

    session_factory = get_sessionmaker()
    with session_factory() as db:
        if not args.keep_existing:
            reset_runtime_data(db)
        seed_demo_data(db, args.completed, args.in_progress, args.abandoned)
        print_summary(db)


if __name__ == "__main__":
    main()

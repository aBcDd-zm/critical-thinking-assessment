from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.interview_blueprint import (
    GeneratedScenarioBlueprint,
    IdentityConstraints,
    build_blueprint_from_generated,
)
from app.agents.scenario_design_agent import (
    GeneratedDynamicInfo,
    GeneratedScenario,
    GeneratedStage,
    GeneratedStageStructure,
    normalize_occupation_key,
)
from app.models.assessment import AssessmentSession
from app.models.participant import Participant, ParticipantProfile
from app.models.scenario import ScenarioStage
from app.services.interview_state_service import InterviewStateService
from app.services.scenario_materialization_service import ScenarioMaterializationService


SKELETON_PROTOTYPES: dict[str, tuple[str, ...]] = {
    "学生": ("课程小组作业", "校园活动协作", "实习选择"),
    "教育培训": ("课程活动安排", "学习资料共建", "班级协作"),
    "医疗健康": ("健康宣教协作", "日常服务排期", "记录整理"),
    "互联网/信息技术": ("功能反馈整理", "小范围试用", "团队协作安排"),
    "工程/制造/建筑": ("日常进度协作", "现场信息整理", "质量检查安排"),
    "商业/金融/管理": ("客户反馈整理", "日常活动安排", "团队任务协作"),
    "政府/公共服务": ("便民信息整理", "社区活动协作", "服务流程安排"),
    "科研/法律/专业服务": ("资料收集协作", "研究任务安排", "文档复核"),
    "文化/传媒/创意": ("内容共创安排", "校对与发布", "活动策划协作"),
    "零售/餐饮/生活服务": ("日常排班协作", "顾客反馈整理", "店内活动安排"),
    "自由职业/个体经营": ("多任务排期", "客户沟通安排", "交付质量检查"),
    "待业/退休/其他": ("社区活动协作", "家庭事务安排", "兴趣小组任务"),
}

FORBIDDEN_INFERRED_ROLES = [
    "负责人",
    "组长",
    "项目协调人",
    "平台运营",
    "产品经理",
    "管理者",
]
RESPONSIBILITY_TERMS = ["负责人", "组长", "班长", "运营", "产品经理", "协调人"]


@dataclass(frozen=True)
class SkeletonSelection:
    task_domain: str
    user_role: str
    identity_constraints: IdentityConstraints


class OccupationSkeletonService:
    """Builds a reviewed, model-free v3.2/v3.3 measurement skeleton."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def prepare(
        self,
        session: AssessmentSession,
        participant: Participant,
        profile: ParticipantProfile,
    ) -> GeneratedScenarioBlueprint:
        profile_payload = profile.ai_profile_json or {}
        selection = self._select(
            participant.industry or "待业/退休/其他",
            participant.career_direction or "参与者",
            profile_payload,
        )
        is_v33 = session.flow_version == "progressive_v3_3"
        arrangements = self._arrangements(selection.task_domain)
        generated = self._build_generated(
            selection.task_domain, selection.user_role, concrete_v33=is_v33
        )
        blueprint = build_blueprint_from_generated(
            generated,
            occupation_category=participant.industry,
            occupation=participant.career_direction,
            user_role=selection.user_role,
            identity_constraints=selection.identity_constraints,
            task_domain=selection.task_domain,
            skeleton_v3_2=not is_v33,
            skeleton_v3_3=is_v33,
            **(arrangements if is_v33 else {}),
        )
        scenario = ScenarioMaterializationService(self.db).materialize(
            generated,
            scenario_code=f"skeleton_v{'33' if is_v33 else '32'}_{uuid4().hex[:20]}",
            source_type="progressive_skeleton",
            occupation_category=participant.industry,
            occupation_key=normalize_occupation_key(
                participant.industry or "待业/退休/其他",
                participant.career_direction or "参与者",
            ),
            model_name=f"deterministic-v{'3.3' if is_v33 else '3.2'}",
            base_scenario_id=None,
            blueprint_override=blueprint,
        )
        session.scenario_id = scenario.id
        first_stage = self.db.execute(
            select(ScenarioStage)
            .where(ScenarioStage.scenario_id == scenario.id)
            .order_by(ScenarioStage.stage_order)
        ).scalars().first()
        if first_stage is None:
            raise ValueError("consultative skeleton did not materialize any stages")
        session.current_stage_id = first_stage.id
        session.selection_mode = (
            "occupation_skeleton_v3_3" if is_v33 else "occupation_skeleton_v3_2"
        )
        session.selection_reason = "deterministic identity-constrained daily-task skeleton"
        session.status = "opening_pending"
        InterviewStateService.initialize(session, scenario)
        self.db.flush()
        return blueprint

    @staticmethod
    def _select(category: str, occupation: str, profile: dict) -> SkeletonSelection:
        tasks = [str(item).strip() for item in profile.get("common_tasks", []) if str(item).strip()]
        collaborators = [
            str(item).strip() for item in profile.get("collaborators", []) if str(item).strip()
        ]
        joined = " ".join([occupation, *tasks, *collaborators])
        prototypes = SKELETON_PROTOTYPES.get(category, SKELETON_PROTOTYPES["待业/退休/其他"])
        if category == "学生" or any(term in occupation for term in ("学生", "大学生", "研究生")):
            if "实习" in joined:
                task_domain = "实习选择"
            elif any(term in joined for term in ("校园", "社团", "活动")):
                task_domain = "校园活动协作"
            else:
                task_domain = "课程小组作业"
            allowed_roles = ["大学生", "小组成员", "参与者"]
            declared_identity = occupation or "学生"
        else:
            task_domain = next(
                (task for task in tasks if 2 <= len(task) <= 30),
                prototypes[0],
            )
            allowed_roles = [occupation or "参与者", "参与者"]
            declared_identity = occupation or "参与者"
        explicit = [term for term in RESPONSIBILITY_TERMS if term in joined]
        allowed_roles.extend(explicit)
        forbidden = [term for term in FORBIDDEN_INFERRED_ROLES if term not in explicit]
        role = explicit[0] if explicit else f"{declared_identity}（参与者）"
        return SkeletonSelection(
            task_domain=task_domain,
            user_role=role,
            identity_constraints=IdentityConstraints(
                declared_identity=declared_identity,
                allowed_roles=list(dict.fromkeys(allowed_roles)),
                forbidden_inferred_roles=forbidden,
                explicit_responsibilities=explicit,
                common_tasks=tasks,
                collaborators=collaborators,
            ),
        )

    @staticmethod
    def _build_generated(
        task: str, user_role: str, *, concrete_v33: bool = False
    ) -> GeneratedScenario:
        arrangements = OccupationSkeletonService._arrangements(task)
        opening = (
            f"你正和其他参与者一起完成{task}，五天后交付，但当前完成度和质量还没核实。"
            if concrete_v33
            else f"你正和其他参与者一起完成{task}，原定五天后交付。"
        )
        conflict = (
            arrangements["stakeholder_conflict"]
            if concrete_v33
            else "部分人希望尽快完成。另一些人担心减少检查会增加返工。"
        )
        decision = (
            "新安排是减少交接和检查，原安排是逐项交接检查，也可只在非关键部分试用。"
            if concrete_v33
            else "可以选择全部改用新安排、先小范围试用，或继续现有安排。"
        )
        return GeneratedScenario(
            professional_knowledge_required=False,
            contains_real_personal_data=False,
            title=f"{task}的协作安排",
            background=(
                f"你以{user_role}的身份参与{task}。大家需要在五天后完成任务，"
                "期间会逐步出现与进度、证据、不同人需求和新情况有关的信息。"
            ),
            central_decision=f"如何在五天内安排{task}，并在新信息出现时调整方案。",
            stages=[
                GeneratedStage(
                    stage_code="s1_problem_definition",
                    context=opening,
                    reference_points=["当前任务", "时间边界"],
                    structure=GeneratedStageStructure(
                        core_fact_ids=["s1_daily_task", "s1_deadline"],
                        condition_relations=["task_must_finish_within_five_days"],
                    ),
                ),
                GeneratedStage(
                    stage_code="s2_evidence_verification",
                    context="最近10次类似任务中有3次延迟。有6名经常参与者赞成减少交接步骤。",
                    reference_points=["记录范围", "参与者范围"],
                    structure=GeneratedStageStructure(
                        core_fact_ids=["s2_delay_record", "s2_support_sample"],
                        condition_relations=["small_familiar_sample_limits_conclusion"],
                    ),
                    dynamic_infos=[GeneratedDynamicInfo(
                        info_code="sample_bias_warning",
                        measurement_function="sample_limitation",
                        title="样本范围",
                        content="这6人都熟悉现有安排，新参与者没有被询问。",
                    )],
                ),
                GeneratedStage(
                    stage_code="s3_stakeholder_perspectives",
                    context=conflict,
                    reference_points=["进度需求", "质量风险"],
                    structure=GeneratedStageStructure(
                        core_fact_ids=["s3_speed_goal", "s3_quality_risk"],
                        condition_relations=["speed_conflicts_with_rework_risk"],
                    ),
                    dynamic_infos=[GeneratedDynamicInfo(
                        info_code="support_capacity_warning",
                        measurement_function="overlooked_stakeholder",
                        title="协作时间",
                        content="有两名参与者只能在最后两天配合，之前无法及时回应。",
                    )],
                ),
                GeneratedStage(
                    stage_code="s4_reasoning_decision",
                    context=decision,
                    reference_points=["可选方案", "初步决定"],
                    structure=GeneratedStageStructure(
                        core_fact_ids=["s4_options", "s4_initial_decision"],
                        condition_relations=["pilot_reduces_exposure"],
                    ),
                    dynamic_infos=[GeneratedDynamicInfo(
                        info_code="competitor_launch_noise",
                        measurement_function="unverified_risk_signal",
                        title="未核实消息",
                        content="有人听说另一组已用类似方法提前完成，但来源尚未确认。",
                    )],
                ),
                GeneratedStage(
                    stage_code="s5_dynamic_adjustment",
                    context="你已经形成初步安排，现在又出现一条可能影响决定的新信息。",
                    reference_points=["原安排", "新信息"],
                    structure=GeneratedStageStructure(
                        core_fact_ids=["s5_prior", "s5_risk", "s5_benefit"],
                        condition_relations=["counterevidence_may_change_decision"],
                    ),
                    dynamic_infos=[
                        GeneratedDynamicInfo(
                            info_code="error_rate_increase",
                            measurement_function="counterevidence_risk",
                            title="风险新信息",
                            content="最新试用中的返工比例从5%升到18%，且集中在关键任务。",
                        ),
                        GeneratedDynamicInfo(
                            info_code="key_user_positive_feedback",
                            measurement_function="counterevidence_benefit",
                            title="收益新信息",
                            content="最新试用的等待时间减少40%，返工比例仍保持在5%。",
                        ),
                    ],
                ),
                GeneratedStage(
                    stage_code="s6_integrated_plan",
                    context="最后五天内只能保证两人持续配合。还需要预留一次检查时间。",
                    reference_points=["资源限制", "检查安排"],
                    structure=GeneratedStageStructure(
                        core_fact_ids=["s6_capacity", "s6_review"],
                        condition_relations=["capacity_limits_parallel_work"],
                    ),
                    dynamic_infos=[GeneratedDynamicInfo(
                        info_code="limited_engineering_capacity",
                        measurement_function="resource_constraint",
                        title="可用时间",
                        content="两名参与者无法同时处理全部调整和大量临时问题。",
                    )],
                ),
            ],
        )

    @staticmethod
    def _arrangements(task: str) -> dict[str, str]:
        return {
            "current_arrangement": "原安排是继续逐项交接检查，确认完成度和质量后再交付",
            "new_arrangement": "新安排是减少交接和检查步骤，以便更快完成任务",
            "pilot_arrangement": "小范围试用是只在非关键部分减少交接，关键部分仍逐项检查",
            "stakeholder_conflict": (
                "一部分参与者想减少交接和检查以赶进度，另一部分担心这样会增加返工和质量风险。"
            ),
            "decision_required": (
                f"你需要决定{task}继续原安排、采用新安排，还是先小范围试用。"
            ),
        }
__all__ = ["OccupationSkeletonService", "SKELETON_PROTOTYPES"]

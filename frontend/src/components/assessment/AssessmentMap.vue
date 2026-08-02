<script setup lang="ts">
import { computed } from "vue";
import type {
  ScenarioSummary,
  SessionProgress,
  StageProgressItem,
  StageSummary,
} from "../../types/session";
import StageTimeline from "./StageTimeline.vue";

const DEFAULT_STAGES: StageProgressItem[] = [
  {
    stage_code: "s1_problem_definition",
    title: "问题界定",
    stage_order: 1,
    status: "active",
    max_followups: 2,
    used_followups: 0,
    used_clarifications: 0,
    can_skip: false,
    skipped: false,
    released_dynamic_info_count: 0,
    estimated_minutes: 5,
  },
  {
    stage_code: "s2_evidence_verification",
    title: "证据评估",
    stage_order: 2,
    status: "pending",
    max_followups: 2,
    used_followups: 0,
    used_clarifications: 0,
    can_skip: false,
    skipped: false,
    released_dynamic_info_count: 0,
    estimated_minutes: 5,
  },
  {
    stage_code: "s3_stakeholder_perspectives",
    title: "多元视角",
    stage_order: 3,
    status: "pending",
    max_followups: 2,
    used_followups: 0,
    used_clarifications: 0,
    can_skip: false,
    skipped: false,
    released_dynamic_info_count: 0,
    estimated_minutes: 5,
  },
  {
    stage_code: "s4_reasoning_decision",
    title: "推理论证",
    stage_order: 4,
    status: "pending",
    max_followups: 2,
    used_followups: 0,
    used_clarifications: 0,
    can_skip: false,
    skipped: false,
    released_dynamic_info_count: 0,
    estimated_minutes: 5,
  },
  {
    stage_code: "s5_dynamic_adjustment",
    title: "动态调整",
    stage_order: 5,
    status: "pending",
    max_followups: 3,
    used_followups: 0,
    used_clarifications: 0,
    can_skip: false,
    skipped: false,
    released_dynamic_info_count: 0,
    estimated_minutes: 6,
  },
  {
    stage_code: "s6_integrated_plan",
    title: "整合决策",
    stage_order: 6,
    status: "pending",
    max_followups: 2,
    used_followups: 0,
    used_clarifications: 0,
    can_skip: false,
    skipped: false,
    released_dynamic_info_count: 0,
    estimated_minutes: 4,
  },
];

const props = defineProps<{
  scenario?: ScenarioSummary | null;
  currentStage?: StageSummary | null;
  progress?: SessionProgress | null;
  status?: string;
}>();

const stages = computed(() => {
  if (props.progress?.stages?.length) return props.progress.stages;
  const currentOrder = props.currentStage?.stage_order ?? 1;
  return DEFAULT_STAGES.map((stage) => ({
    ...stage,
    status:
      stage.stage_order < currentOrder
        ? "completed"
        : stage.stage_order === currentOrder
          ? "active"
          : "pending",
  }));
});

const totalStages = computed(() => props.progress?.total_stages || stages.value.length || 6);
const currentOrder = computed(() => props.progress?.current_stage_order || props.currentStage?.stage_order || 1);
const progressPercent = computed(() => {
  const total = Math.max(totalStages.value, 1);
  return Math.min(Math.max((currentOrder.value / total) * 100, 8), 100);
});
const currentStageTitle = computed(() => props.currentStage?.title || stages.value.find((stage) => stage.status === "active")?.title || "测评准备");
const estimatedMinutes = computed(() => props.progress?.estimated_minutes || props.scenario?.estimated_minutes || 30);
const elapsedText = computed(() => {
  const seconds = props.progress?.elapsed_seconds;
  if (!seconds || seconds < 60) return "刚刚开始";
  return `${Math.floor(seconds / 60)} 分钟`;
});
</script>

<template>
  <aside class="assessment-map" aria-label="测评地图">
    <div class="map-head">
      <span class="map-kicker">测评地图</span>
      <h2>{{ scenario?.title || "审辩式思维测评" }}</h2>
      <p>{{ estimatedMinutes }} 分钟内完成一个管理决策情境。</p>
    </div>

    <div class="map-progress-card">
      <div>
        <span>当前阶段</span>
        <strong>阶段 {{ currentOrder }} / {{ totalStages }}</strong>
      </div>
      <p>{{ currentStageTitle }}</p>
      <div class="map-progress-track" aria-hidden="true">
        <i :style="{ width: `${progressPercent}%` }"></i>
      </div>
    </div>

    <StageTimeline :stages="stages" />

    <div class="map-foot">
      <span>{{ status === "in_progress" ? "测评进行中" : status || "连接中" }}</span>
      <span>{{ elapsedText }}</span>
    </div>
  </aside>
</template>

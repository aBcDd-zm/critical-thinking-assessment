<script setup lang="ts">
import { computed } from "vue";
import type { DimensionReport } from "../../types/report";

const props = defineProps<{
  dimensionReports: DimensionReport[];
}>();

function isSupportedScore(item: DimensionReport): boolean {
  return item.score_kind === "supported" && item.score != null;
}

const averageScore = computed(() => {
  const scored = props.dimensionReports.filter(isSupportedScore);
  if (!scored.length) return "暂不评分";
  const total = scored.reduce((sum, item) => sum + (item.score || 0), 0);
  return (total / scored.length).toFixed(1);
});

const scoredCount = computed(
  () => props.dimensionReports.filter(isSupportedScore).length,
);

const averageLabel = computed(() =>
  scoredCount.value === 6 ? "综合得分" : "已评分维度均分",
);

const overallEsi = computed(() => {
  const scored = props.dimensionReports.filter(
    (item) => isSupportedScore(item) && item.evidence_sufficiency_index != null,
  );
  if (!scored.length) return null;
  const mean = scored.reduce(
    (sum, item) => sum + (item.evidence_sufficiency_index || 0), 0,
  ) / scored.length;
  return Math.round(mean * (scored.length / 6));
});

function esiLevelLabel(level: DimensionReport["evidence_sufficiency_level"]): string {
  return level === "high" ? "高" : level === "medium" ? "中" : level === "low" ? "低" : "";
}

function scoreColorClass(score: number | null): string {
  if (score == null) return "score-unavailable";
  if (score <= 2) return "score-low";
  if (score === 3) return "score-medium";
  return "score-high";
}
function scoreDisplay(item: DimensionReport): string | number {
  if (isSupportedScore(item)) return item.score as number;
  return item.score_kind === "provisional" ? "暂不评分" : "未测到";
}
</script>

<template>
  <section class="report-panel report-section score-overview">
    <h2>六维评分概览</h2>

    <div class="average-score">
      <span class="average-label">{{ averageLabel }}</span>
      <strong class="average-value">{{ averageScore }}</strong>
      <span v-if="averageScore !== '暂不评分'" class="average-scale">/ 5</span>
    </div>
    <p v-if="scoredCount > 0 && scoredCount < 6" class="partial-score-note">
      当前仅有 {{ scoredCount }}/6 个维度达到评分条件，该均分不代表六维综合水平。
    </p>
    <p v-if="overallEsi != null" class="overall-esi">
      整体证据基础指数（ESI） {{ overallEsi }}/100
      <small>已按已评分维度覆盖率折算</small>
    </p>

    <ul class="score-list">
      <li
        v-for="item in dimensionReports"
        :key="item.dimension_key"
        class="score-row"
      >
        <span class="score-name">{{ item.dimension_name }}</span>
        <div class="score-bar">
          <div
            class="score-bar-fill"
            :class="scoreColorClass(isSupportedScore(item) ? item.score : null)"
            :style="{ width: isSupportedScore(item) ? `${((item.score || 0) / 5) * 100}%` : '0%' }"
          ></div>
        </div>
        <span class="score-value" :class="scoreColorClass(isSupportedScore(item) ? item.score : null)">
          {{ scoreDisplay(item) }}
        </span>
        <small class="score-esi">
          <template v-if="item.evidence_sufficiency_index != null">
            ESI {{ item.evidence_sufficiency_index }}/100 · {{ esiLevelLabel(item.evidence_sufficiency_level) }}
          </template>
          <template v-else>无测量依据</template>
          <strong v-if="item.score_kind === 'provisional'" class="score-evidence-gate">
            关键评分证据：未达标
          </strong>
        </small>
      </li>
    </ul>
    <p v-if="dimensionReports.some((item) => item.score_kind === 'provisional')" class="partial-score-note">
      ESI 反映本次作答机会与证据基础；仍缺关键证据的维度即使 ESI 较高，也不会进入能力评分。
    </p>
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue";
import type { DimensionReport } from "../../types/report";
import EvidenceQuote from "./EvidenceQuote.vue";

const props = defineProps<{
  dimensionReport: DimensionReport;
  index: number;
}>();

const paddedIndex = computed(() => String(props.index + 1).padStart(2, "0"));

const scoreColorClass = computed(() => {
  const score = props.dimensionReport.score_kind === "supported"
    ? props.dimensionReport.score
    : null;
  if (score == null) return "score-unavailable";
  if (score <= 2) return "score-low";
  if (score === 3) return "score-medium";
  return "score-high";
});

const hasEvidence = computed(
  () => props.dimensionReport.evidence_quotes && props.dimensionReport.evidence_quotes.length > 0,
);

const esiLevelLabel = computed(() => ({
  low: "低",
  medium: "中",
  high: "高",
} as Record<string, string>)[props.dimensionReport.evidence_sufficiency_level || ""] || "");
const scoreDisplay = computed(() => {
  if (
    props.dimensionReport.score_kind === "supported" &&
    props.dimensionReport.score != null
  ) {
    return String(props.dimensionReport.score);
  }
  return props.dimensionReport.score_kind === "provisional" ? "暂不评分" : "未测到";
});
</script>

<template>
  <article class="report-panel report-section dimension-report-card">
    <header class="dimension-report-header">
      <span class="dimension-index">{{ paddedIndex }}</span>
      <div class="dimension-title">
        <strong>{{ dimensionReport.dimension_name }}</strong>
        <span class="dimension-level">{{ dimensionReport.level_label }}</span>
      </div>
      <div class="dimension-score-badge" :class="scoreColorClass">
        <span class="score-number">{{ scoreDisplay }}</span>
        <span
          v-if="dimensionReport.score_kind === 'supported' && dimensionReport.score != null"
          class="score-scale"
        >/5</span>
      </div>
    </header>

    <div class="dimension-report-body">
      <div class="report-field evidence-sufficiency-field">
        <span class="report-field-label">证据基础指数（ESI）</span>
        <p v-if="dimensionReport.evidence_sufficiency_index != null">
          <strong>{{ dimensionReport.evidence_sufficiency_index }}/100 · {{ esiLevelLabel }}</strong>
          <small>{{ dimensionReport.evidence_sufficiency_note }}</small>
        </p>
        <p v-else-if="dimensionReport.score_kind === 'provisional'">
          已获得部分相关证据，但暂无可用的 ESI 数值。
        </p>
        <p v-else>未获得该维度的公平作答机会，因此未测到。</p>
        <small v-if="dimensionReport.score_kind === 'provisional'" class="provisional-note">
          <strong>关键评分证据：未达标</strong>
          <span>已获得部分相关证据，但仍缺关键项，因此暂不评分。</span>
        </small>
      </div>
      <div class="report-field">
        <span class="report-field-label">
          {{ dimensionReport.score_kind === "supported" ? "优势" : "证据说明" }}
        </span>
        <p>{{ dimensionReport.strength }}</p>
      </div>

      <div v-if="dimensionReport.weakness" class="report-field">
        <span class="report-field-label">待加强</span>
        <p>{{ dimensionReport.weakness }}</p>
      </div>

      <div v-if="hasEvidence" class="report-field">
        <span class="report-field-label">证据引用</span>
        <div class="evidence-list">
          <EvidenceQuote
            v-for="(quote, quoteIndex) in dimensionReport.evidence_quotes"
            :key="`quote-${quoteIndex}`"
            :quote="quote"
          />
        </div>
      </div>
      <p v-else class="report-field-empty">
        <template v-if="dimensionReport.score_kind === 'provisional'">
          已获得部分有效证据，但尚不足以形成可引用的评分证据，因而暂不评分。
        </template>
        <template v-else>
          本次对话未提供该维度的有效证据。
        </template>
      </p>

      <div class="report-field">
        <span class="report-field-label">建议</span>
        <p>{{ dimensionReport.suggestion }}</p>
      </div>
    </div>
  </article>
</template>

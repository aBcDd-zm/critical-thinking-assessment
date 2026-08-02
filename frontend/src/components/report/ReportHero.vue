<script setup lang="ts">
import { computed } from "vue";
import { RouterLink } from "vue-router";

const props = defineProps<{
  nickname?: string | null;
  scenarioTitle?: string | null;
  overallLevel?: string;
  summary?: string;
  sessionUuid: string;
  fallbackUsed?: boolean;
  measurementQualityStatus?: "valid" | "caution" | "invalid";
  warnings?: string[];
  pdfAvailable?: boolean;
  pdfDownloading?: boolean;
  downloadMessage?: string;
}>();

const emit = defineEmits<{
  "download-pdf": [];
}>();

const warningText = computed(() => (props.warnings || []).join(" "));
const confidenceNotice = computed(() => {
  if (props.fallbackUsed) {
    return props.measurementQualityStatus === "valid"
      ? "报告文字采用确定性降级生成；测量质量仍为有效"
      : "报告文字采用确定性降级生成；测量质量请以下方提示为准";
  }
  if (
    /limited_evidence|low_confidence|VALIDATION_ERROR|MODEL_ERROR/i.test(
      warningText.value,
    )
  ) {
    return "本次存在证据充分性或模型生成提示，请查看报告说明";
  }
  return "";
});

</script>

<template>
  <section class="report-hero">
    <div>
      <p class="assessment-kicker">审辩式思维动态测评 · 测评报告</p>
      <h1>{{ nickname || "受测者" }} 的审辩式思维动态测评报告</h1>
      <p v-if="scenarioTitle" class="report-scenario">情境：{{ scenarioTitle }}</p>
      <p class="report-summary">{{ summary }}</p>
      <div v-if="overallLevel" class="report-level-row">
        <span class="report-level-badge">{{ overallLevel }}</span>
        <span v-if="confidenceNotice" class="report-confidence-note">
          {{ confidenceNotice }}
        </span>
      </div>
    </div>
    <div class="report-hero-actions">
      <button
        v-if="pdfAvailable"
        class="assessment-primary report-download-button"
        type="button"
        :disabled="pdfDownloading"
        @click="emit('download-pdf')"
      >
        {{ pdfDownloading ? "正在生成 PDF..." : "下载 PDF" }}
      </button>
      <RouterLink
        class="assessment-secondary"
        to="/assessment"
      >
        返回测评首页
      </RouterLink>
      <small v-if="downloadMessage" class="report-download-message">{{ downloadMessage }}</small>
    </div>
  </section>
</template>

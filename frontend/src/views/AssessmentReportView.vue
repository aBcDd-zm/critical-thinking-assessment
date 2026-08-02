<script setup lang="ts">
import axios from "axios";
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  downloadAssessmentReportPdf,
  getAssessmentFeedback,
  getAssessmentReport,
  getAssessmentSession,
  requestAssessmentReportGeneration,
  submitAssessmentFeedback,
} from "../api/session";
import AdvantageList from "../components/report/AdvantageList.vue";
import DimensionReportCard from "../components/report/DimensionReportCard.vue";
import EmptyReportState from "../components/report/EmptyReportState.vue";
import ImprovementPlan from "../components/report/ImprovementPlan.vue";
import ReportDisclaimer from "../components/report/ReportDisclaimer.vue";
import ReportHero from "../components/report/ReportHero.vue";
import ReportSkeleton from "../components/report/ReportSkeleton.vue";
import ScoreOverview from "../components/report/ScoreOverview.vue";
import { shouldShowInterpretiveResults } from "../utils/reportVisibility";
import type {
  AssessmentReportResponse,
  FeedbackResponse,
  SessionResponse,
  SubmitFeedbackPayload,
} from "../types/session";
import type { DimensionReport } from "../types/report";
import type { MeasurementQuality } from "../types/report";

const route = useRoute();
const router = useRouter();
const sessionUuid = computed(() => String(route.params.sessionUuid || ""));
const session = ref<SessionResponse | null>(null);
const report = ref<AssessmentReportResponse | null>(null);
const reportReady = computed(() => Boolean(report.value));
const loading = ref(false);
const feedbackLoading = ref(false);
const reportMissing = ref(false);
const error = ref("");
const feedbackSubmitting = ref(false);
const feedbackSubmitted = ref(false);
const feedbackMessage = ref("");
const pdfDownloading = ref(false);
const pdfMessage = ref("");
const feedbackForm = ref<SubmitFeedbackPayload>({
  realism_score: 4,
  difficulty_score: 3,
  naturalness_score: 4,
  fatigue_score: 3,
  report_trust_score: 4,
  overall_satisfaction_score: 4,
  open_feedback: "",
});

const RETRY_DELAYS = [1000, 2000, 4000, 8000, 15000, 30000, 30000, 30000];

type LegacyDimensionScore = {
  dimension_key?: string;
  dimension_name?: string;
  score?: number;
  evidence?: string;
};

const reportContent = computed<Record<string, unknown>>(
  () => (report.value?.report || {}) as unknown as Record<string, unknown>,
);

const overallLevelLabel = computed(() => normalizeLevelLabel(report.value?.report.overall_level));
const sessionStatusLabel = computed(() => normalizeSessionStatus(session.value?.status));
const measurementQuality = computed<MeasurementQuality | null>(() => {
  const value = reportContent.value.measurement_quality;
  return value && typeof value === "object" ? value as MeasurementQuality : null;
});
const interpretiveResultsVisible = computed(() =>
  shouldShowInterpretiveResults(measurementQuality.value?.status),
);

const dimensionReports = computed<DimensionReport[]>(() => {
  const current = reportContent.value.dimension_reports;
  if (Array.isArray(current)) {
    return (current as DimensionReport[]).map((item) => {
      const scoreKind = item.score_kind || (item.score == null ? "unobserved" : "supported");
      const normalizedScore = scoreKind === "supported" ? item.score : null;
      return {
        ...item,
        score: normalizedScore,
        level_label:
          scoreKind === "supported"
            ? normalizeLevelLabel(item.level_label, normalizedScore)
            : scoreKind === "provisional"
              ? "暂不评分"
              : "未测到",
        assessment_status:
          normalizedScore == null
            ? "insufficient_evidence"
            : item.assessment_status || "scored",
        evidence_sufficiency_index: item.evidence_sufficiency_index ?? null,
        evidence_sufficiency_level: item.evidence_sufficiency_level ?? null,
        score_kind: scoreKind,
        evidence_sufficiency_note: item.evidence_sufficiency_note || "",
      };
    });
  }

  const legacy = reportContent.value.dimension_scores;
  if (!Array.isArray(legacy)) return [];
  return (legacy as LegacyDimensionScore[]).map((item, index) => ({
    dimension_key: item.dimension_key || `dimension_${index + 1}`,
    dimension_name: item.dimension_name || `维度 ${index + 1}`,
    score: Number(item.score || 0),
    assessment_status: "scored",
    level_label: "",
    strength: item.evidence || "",
    weakness: null,
    evidence_quotes: item.evidence ? [item.evidence] : [],
    suggestion: "",
    evidence_sufficiency_index: null,
    evidence_sufficiency_level: null,
    score_kind: "supported",
    evidence_sufficiency_note: "Legacy 报告未计算 ESI。",
  }));
});

const advantages = computed<string[]>(() => {
  const value = reportContent.value.advantages ?? reportContent.value.strengths;
  return Array.isArray(value) ? value.map(String) : [];
});

const improvementSuggestions = computed<string[]>(() => {
  const value = reportContent.value.improvement_suggestions;
  return Array.isArray(value) ? value.map(String) : [];
});

const developmentPlan = computed<string[]>(() => {
  const value = reportContent.value.development_plan;
  if (Array.isArray(value)) return value.map(String);
  if (value && typeof value === "object") return Object.values(value).map(String);
  return [];
});

const disclaimer = computed(
  () =>
    String(reportContent.value.disclaimer || "") ||
    "本报告基于本次情境对话生成，仅用于学习与发展参考。",
);
const reportScenarioTitle = computed(() =>
  session.value?.scenario.source_type === "ai_base" ||
  session.value?.scenario.source_type === "ai_adapted"
    ? "职业适配协作判断情景"
    : session.value?.scenario.title,
);
const formalTurnCount = computed(
  () => session.value?.turns.filter((turn) => !turn.content_type.startsWith("profile_")).length || 0,
);

async function fetchReportWithRetry(): Promise<AssessmentReportResponse> {
  let lastError: unknown = null;
  let recoveryRequested = false;
  for (let attempt = 0; attempt <= RETRY_DELAYS.length; attempt++) {
    try {
      return await getAssessmentReport(sessionUuid.value);
    } catch (err) {
      lastError = err;
      const status = axios.isAxiosError(err) ? err.response?.status : null;
      if (status === 404 && attempt < RETRY_DELAYS.length) {
        if (!recoveryRequested) {
          recoveryRequested = true;
          try {
            await requestAssessmentReportGeneration(sessionUuid.value);
          } catch {
            // The original final-turn task may still finish; keep the bounded
            // read retry even if this best-effort recovery request fails.
          }
        }
        await new Promise((resolve) => setTimeout(resolve, RETRY_DELAYS[attempt]));
        continue;
      }
      throw err;
    }
  }
  throw lastError;
}

async function loadReport(force = false) {
  if (!sessionUuid.value) return;
  loading.value = true;
  error.value = "";
  reportMissing.value = false;
  report.value = null;

  try {
    session.value = await getAssessmentSession(sessionUuid.value);
    report.value = await fetchReportWithRetry();
    loading.value = false;
    feedbackLoading.value = true;
    await loadFeedback();
  } catch (err) {
    if (axios.isAxiosError(err) && err.response?.status === 404) {
      reportMissing.value = true;
    } else {
      error.value =
        "报告读取失败，请确认后端服务和数据库已经启动，或稍后重试。";
    }
  } finally {
    loading.value = false;
    feedbackLoading.value = false;
  }

  if (force) {
    const { fresh, ...remainingQuery } = route.query;
    await router.replace({ query: remainingQuery });
  }
}

async function loadFeedback() {
  try {
    const state = await getAssessmentFeedback(sessionUuid.value);
    feedbackSubmitted.value = state.submitted;
    if (state.submitted && state.feedback) {
      feedbackForm.value = toFeedbackForm(state.feedback);
      feedbackMessage.value = "已收到你的反馈，感谢参与本次测评。";
    } else {
      feedbackMessage.value = "";
    }
  } catch {
    feedbackSubmitted.value = false;
    feedbackMessage.value = "反馈状态暂时无法读取，不影响查看报告。";
  }
}

async function downloadPdf() {
  if (pdfDownloading.value || !sessionUuid.value || !reportReady.value) return;
  pdfDownloading.value = true;
  pdfMessage.value = "";
  try {
    const blob = await downloadAssessmentReportPdf(sessionUuid.value);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const nickname = (session.value?.participant_nickname || "受测者")
      .replace(/[\\/:*?"<>|\s]+/g, "-")
      .replace(/^-+|-+$/g, "");
    link.href = url;
    link.download = `审辩式思维动态测评报告-${nickname || "受测者"}.pdf`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    pdfMessage.value = "PDF 已生成。";
  } catch {
    pdfMessage.value = "PDF 下载失败，请稍后重试。";
  } finally {
    pdfDownloading.value = false;
  }
}

async function submitFeedback() {
  if (!reportReady.value) return;
  feedbackSubmitting.value = true;
  feedbackMessage.value = "";
  try {
    const feedback = await submitAssessmentFeedback(sessionUuid.value, feedbackForm.value);
    feedbackForm.value = toFeedbackForm(feedback);
    feedbackSubmitted.value = true;
    feedbackMessage.value = "反馈已保存。你的评价会用于后续优化测评情境、追问策略和报告质量。";
  } catch (err) {
    feedbackMessage.value = "反馈提交失败，请稍后重试。";
  } finally {
    feedbackSubmitting.value = false;
  }
}

function toFeedbackForm(feedback: FeedbackResponse): SubmitFeedbackPayload {
  return {
    realism_score: feedback.realism_score,
    difficulty_score: feedback.difficulty_score,
    naturalness_score: feedback.naturalness_score,
    fatigue_score: feedback.fatigue_score,
    report_trust_score: feedback.report_trust_score,
    overall_satisfaction_score: feedback.overall_satisfaction_score,
    open_feedback: feedback.open_feedback || "",
  };
}

function normalizeLevelLabel(value?: string | null, score?: number | null) {
  const labels: Record<string, string> = {
    low: "明显不足",
    medium: "中等",
    high: "较强",
    低: "明显不足",
    中: "中等",
    高: "较强",
    初级: "基础",
    较高: "较强",
    明显不足: "明显不足",
    基础: "基础",
    突出: "突出",
    暂不评分: "暂不评分",
  };
  const label = String(value || "").trim();
  if (["明显不足", "基础", "中等", "较强", "突出", "暂不评分"].includes(label)) {
    return label;
  }
  if (score != null) {
    if (score <= 1) return "明显不足";
    if (score === 2) return "基础";
    if (score === 3) return "中等";
    if (score === 4) return "较强";
    return "突出";
  }
  if (labels[label]) return labels[label];
  if (label) return label;
  return "暂不评分";
}

function normalizeSessionStatus(value?: string | null) {
  const labels: Record<string, string> = {
    created: "已创建",
    in_progress: "测评中",
    generating: "正在生成报告",
    completed: "已完成",
    terminated: "已结束",
  };
  return labels[String(value || "")] || "状态未知";
}

onMounted(() => {
  const forceFresh = route.query.fresh === "1";
  void loadReport(forceFresh);
});

watch(
  () => route.params.sessionUuid,
  () => {
    void loadReport();
  },
);
</script>

<template>
  <main class="report-page immersive-report">
    <ReportHero
      :nickname="session?.participant_nickname"
      :scenario-title="reportScenarioTitle"
      :overall-level="overallLevelLabel"
      :summary="report?.report.summary"
      :session-uuid="sessionUuid"
      :fallback-used="report?.report.fallback_used"
      :measurement-quality-status="measurementQuality?.status"
      :warnings="report?.report.warnings"
      :pdf-available="reportReady"
      :pdf-downloading="pdfDownloading"
      :download-message="pdfMessage"
      @download-pdf="downloadPdf"
    />

    <section v-if="session" class="report-panel session-summary">
      <div>
        <span class="console-label">受测者</span>
        <strong>{{ session.participant_nickname }}</strong>
        <small>{{ reportScenarioTitle }} · {{ sessionStatusLabel }}</small>
      </div>
      <div>
        <span class="console-label">对话轮次</span>
        <strong>{{ formalTurnCount }}</strong>
        <small>已记录对话轮次</small>
      </div>
    </section>

    <template v-if="loading">
      <section class="report-panel report-generation-status" role="status">
        <span class="console-label">报告生成中</span>
        <h2>访谈已经完成，正在整理评分与报告</h2>
        <p>页面会自动刷新，请不要重复提交或关闭页面。</p>
      </section>
      <ReportSkeleton />
    </template>

    <EmptyReportState
      v-else-if="reportMissing"
      :error="error"
      @retry="loadReport"
    />

    <p v-else-if="error" class="assessment-error report-error">{{ error }}</p>

    <template v-if="report">
      <section
        v-if="measurementQuality?.status === 'invalid' || measurementQuality?.status === 'caution'"
        class="report-panel report-section measurement-quality-alert"
        :class="measurementQuality.status === 'invalid' ? 'is-invalid' : 'is-caution'"
      >
        <div>
          <span class="console-label">测量质量</span>
          <h2>
            {{
              measurementQuality.status === "invalid"
                ? "测评过程异常，结果不宜解释"
                : "本次报告仅形成部分可解释结果"
            }}
          </h2>
          <p>
            {{
              measurementQuality.status === "invalid"
                ? "建议重新测评。本次历史对话和原始分数不会被改写。"
                : "未测到或证据未充分的维度不会被解释为能力不足。"
            }}
          </p>
          <ul v-if="measurementQuality.reasons?.length">
            <li v-for="reason in measurementQuality.reasons" :key="reason">{{ reason }}</li>
          </ul>
        </div>
        <button
          v-if="measurementQuality.retest_recommended"
          type="button"
          class="console-button"
          @click="router.push('/assessment')"
        >
          重新测评
        </button>
      </section>

      <template v-if="interpretiveResultsVisible">
        <ScoreOverview :dimension-reports="dimensionReports" />

        <DimensionReportCard
          v-for="(item, index) in dimensionReports"
          :key="item.dimension_key"
          :dimension-report="item"
          :index="index"
        />

        <AdvantageList :items="advantages" />

        <ImprovementPlan
          :suggestions="improvementSuggestions"
          :plan="developmentPlan"
        />
      </template>

      <ReportDisclaimer :disclaimer="disclaimer" />
    </template>

    <section
      v-if="session && reportReady && !feedbackLoading"
      class="report-panel feedback-panel"
    >
      <div class="feedback-heading">
        <div>
          <span class="console-label">体验反馈</span>
          <h2>本次测评体验反馈</h2>
        </div>
        <span v-if="feedbackSubmitted" class="status-pill">已保存</span>
      </div>

      <div class="feedback-grid">
        <label>
          <span>情境真实感</span>
          <input v-model.number="feedbackForm.realism_score" type="range" min="1" max="5" />
          <strong>{{ feedbackForm.realism_score }}</strong>
        </label>
        <label>
          <span>任务难度</span>
          <input v-model.number="feedbackForm.difficulty_score" type="range" min="1" max="5" />
          <strong>{{ feedbackForm.difficulty_score }}</strong>
        </label>
        <label>
          <span>追问自然度</span>
          <input v-model.number="feedbackForm.naturalness_score" type="range" min="1" max="5" />
          <strong>{{ feedbackForm.naturalness_score }}</strong>
        </label>
        <label>
          <span>疲劳感</span>
          <input v-model.number="feedbackForm.fatigue_score" type="range" min="1" max="5" />
          <strong>{{ feedbackForm.fatigue_score }}</strong>
        </label>
        <label>
          <span>报告可信度</span>
          <input v-model.number="feedbackForm.report_trust_score" type="range" min="1" max="5" />
          <strong>{{ feedbackForm.report_trust_score }}</strong>
        </label>
        <label>
          <span>总体满意度</span>
          <input
            v-model.number="feedbackForm.overall_satisfaction_score"
            type="range"
            min="1"
            max="5"
          />
          <strong>{{ feedbackForm.overall_satisfaction_score }}</strong>
        </label>
      </div>

      <label class="feedback-open">
        <span>还有哪些地方让你觉得自然、奇怪或需要改进？</span>
        <textarea
          v-model="feedbackForm.open_feedback"
          maxlength="2000"
          placeholder="可以简单写几句，例如情境是否真实、追问是否突兀、报告是否有帮助。"
        ></textarea>
      </label>

      <div class="feedback-actions">
        <p v-if="feedbackMessage" class="feedback-message">{{ feedbackMessage }}</p>
        <button
          class="assessment-primary"
          type="button"
          :disabled="feedbackSubmitting"
          @click="submitFeedback"
        >
          {{ feedbackSubmitting ? "提交中..." : feedbackSubmitted ? "更新反馈" : "提交反馈" }}
        </button>
      </div>
    </section>
  </main>
</template>

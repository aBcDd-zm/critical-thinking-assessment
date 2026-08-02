<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api } from "../api/client";
import type { DashboardAnalytics, DashboardSummary } from "../types/admin";

const summary = ref<DashboardSummary | null>(null);
const analytics = ref<DashboardAnalytics | null>(null);
const loading = ref(false);
const loadError = ref("");

const configMetrics = [
  ["情境", "scenario_count", "active_scenario_count"],
  ["阶段", "stage_count", null],
  ["动态信息", "dynamic_info_count", null],
  ["追问策略", "intervention_rule_count", null],
  ["能力维度", "rubric_dimension_count", null],
  ["评分锚点", "rubric_anchor_count", null],
  ["Prompt 模板", "prompt_template_count", null],
  ["报告模板", "report_template_count", null],
] as const;

const operationMetrics = computed(() => [
  {
    label: "测评会话",
    value: analytics.value?.session_count ?? 0,
    hint: `${analytics.value?.in_progress_session_count ?? 0} 个进行中`,
  },
  {
    label: "完成率",
    value: `${analytics.value?.completion_rate ?? 0}%`,
    hint: `${analytics.value?.completed_session_count ?? 0} 个已完成`,
  },
  {
    label: "平均耗时",
    value:
      analytics.value?.average_duration_minutes == null
        ? "-"
        : `${analytics.value.average_duration_minutes} 分钟`,
    hint: "基于已完成测评",
  },
  {
    label: "平均轮次",
    value: analytics.value?.average_turn_count == null ? "-" : analytics.value.average_turn_count,
    hint: `${analytics.value?.dialogue_turn_count ?? 0} 条对话记录`,
  },
]);

const traceMetrics = computed(() => [
  {
    label: "Agent 调用",
    value: analytics.value?.agent_trace_count ?? 0,
    hint:
      analytics.value?.agent_success_rate == null
        ? "暂无调用质量数据"
        : `成功率 ${analytics.value.agent_success_rate}%`,
  },
  {
    label: "报告",
    value: analytics.value?.report_count ?? 0,
    hint: "最终报告落库数",
  },
  {
    label: "评分快照",
    value: analytics.value?.score_snapshot_count ?? 0,
    hint: `${analytics.value?.score_result_count ?? 0} 条维度评分`,
  },
  {
    label: "证据句",
    value: analytics.value?.score_evidence_count ?? 0,
    hint: "可解释评分引用",
  },
]);

const feedbackMetrics = computed(() => [
  {
    label: "反馈提交",
    value: analytics.value?.feedback_count ?? 0,
    hint: `覆盖率 ${analytics.value?.feedback_coverage_rate ?? 0}%`,
  },
  {
    label: "总体满意度",
    value: formatScore(analytics.value?.feedback_averages.overall_satisfaction_score),
    hint: `${analytics.value?.low_satisfaction_count ?? 0} 个低满意反馈`,
  },
  {
    label: "追问自然度",
    value: formatScore(analytics.value?.feedback_averages.naturalness_score),
    hint: "用户感知的对话流畅度",
  },
  {
    label: "报告可信度",
    value: formatScore(analytics.value?.feedback_averages.report_trust_score),
    hint: "用户对结果解释的信任",
  },
]);

const feedbackScoreRows = computed(() => [
  {
    label: "情境真实感",
    value: analytics.value?.feedback_averages.realism_score,
    hint: "情境是否接近真实管理决策",
  },
  {
    label: "任务难度",
    value: analytics.value?.feedback_averages.difficulty_score,
    hint: "难度是否适合 30 分钟测评",
  },
  {
    label: "追问自然度",
    value: analytics.value?.feedback_averages.naturalness_score,
    hint: "追问是否顺着用户回答推进",
  },
  {
    label: "疲劳感",
    value: analytics.value?.feedback_averages.fatigue_score,
    hint: "数值越高代表用户越疲劳",
  },
  {
    label: "报告可信度",
    value: analytics.value?.feedback_averages.report_trust_score,
    hint: "报告解释是否让用户信服",
  },
  {
    label: "总体满意度",
    value: analytics.value?.feedback_averages.overall_satisfaction_score,
    hint: "用户对完整测评体验的主观评价",
  },
]);

const hasRuntimeData = computed(() => Boolean(analytics.value?.session_count));
const hasFeedbackData = computed(() => Boolean(analytics.value?.feedback_count));

function metricValue(key: keyof DashboardSummary) {
  return summary.value?.[key] ?? 0;
}

function formatScore(value: number | null | undefined) {
  return value == null ? "-" : value.toFixed(2);
}

function scoreBarWidth(value: number | null | undefined) {
  if (value == null) return "0%";
  return `${Math.min(100, Math.max(0, (value / 5) * 100))}%`;
}

function formatDate(value: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return `${date.getMonth() + 1}/${date.getDate()} ${String(date.getHours()).padStart(
    2,
    "0",
  )}:${String(date.getMinutes()).padStart(2, "0")}`;
}

async function load() {
  loading.value = true;
  loadError.value = "";
  try {
    const [{ data: summaryData }, { data: analyticsData }] = await Promise.all([
      api.get<DashboardSummary>("/admin/dashboard/summary"),
      api.get<DashboardAnalytics>("/admin/dashboard/analytics"),
    ]);
    summary.value = summaryData;
    analytics.value = analyticsData;
  } catch {
    loadError.value = "后台数据加载失败，请重新登录或稍后重试。";
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <section class="page-stack dashboard-page">
    <div class="analytics-hero">
      <div>
        <p class="eyebrow">Assessment Operations</p>
        <h2>测评数据分析看板</h2>
        <p>
          汇总配置资产、测评会话、Agent 调用、评分证据与报告产出，用于复盘测评质量和后续迭代方向。
        </p>
      </div>
      <div class="summary-badges">
        <span class="status-pill">配置驱动</span>
        <span class="status-pill muted-pill">过程可追踪</span>
      </div>
    </div>

    <div v-if="loading" class="panel">正在加载后台分析数据...</div>
    <div v-else-if="loadError" class="panel error">{{ loadError }}</div>

    <template v-else>
      <section class="analytics-section">
        <div class="section-heading">
          <div>
            <p class="eyebrow">Runtime</p>
            <h2>测评运行概览</h2>
          </div>
          <button class="ghost-button" type="button" @click="load">刷新数据</button>
        </div>
        <div class="metric-grid analytics-metrics">
          <article v-for="item in operationMetrics" :key="item.label" class="metric metric-large">
            <span>{{ item.label }}</span>
            <strong>{{ item.value }}</strong>
            <small>{{ item.hint }}</small>
          </article>
        </div>
      </section>

      <section class="analytics-grid">
        <article class="page-section">
          <div class="section-heading compact-heading">
            <h2>Agent 与评分链路</h2>
            <span class="muted">模型调用、报告、证据句</span>
          </div>
          <div class="trace-metric-list">
            <div v-for="item in traceMetrics" :key="item.label" class="trace-metric">
              <span>{{ item.label }}</span>
              <strong>{{ item.value }}</strong>
              <small>{{ item.hint }}</small>
            </div>
          </div>
        </article>

        <article class="page-section">
          <div class="section-heading compact-heading">
            <h2>会话状态分布</h2>
            <span class="muted">测评漏斗</span>
          </div>
          <div v-if="analytics?.status_distribution.length" class="status-bars">
            <div v-for="item in analytics.status_distribution" :key="item.status" class="status-bar">
              <div>
                <span>{{ item.status }}</span>
                <strong>{{ item.count }}</strong>
              </div>
              <i
                :style="{
                  width: `${Math.max(
                    8,
                    (item.count / Math.max(analytics.session_count, 1)) * 100,
                  )}%`,
                }"
              ></i>
            </div>
          </div>
          <p v-else class="empty-state">暂无会话数据。</p>
        </article>
      </section>

      <section class="analytics-grid wide-left">
        <article class="page-section">
          <div class="section-heading compact-heading">
            <h2>最近测评记录</h2>
            <span class="muted">最近 8 条会话</span>
          </div>
          <table v-if="analytics?.recent_sessions.length" class="data-table compact-table">
            <thead>
              <tr>
                <th>用户</th>
                <th>情境</th>
                <th>状态</th>
                <th>轮次</th>
                <th>Agent</th>
                <th>报告</th>
                <th>更新时间</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in analytics.recent_sessions" :key="item.session_uuid">
                <td>{{ item.nickname }}</td>
                <td>{{ item.scenario_title }}</td>
                <td>
                  <span class="mini-pill">{{ item.status }}</span>
                </td>
                <td>{{ item.turn_count }}</td>
                <td>{{ item.agent_trace_count }}</td>
                <td>{{ item.report_status || "-" }}</td>
                <td>{{ formatDate(item.updated_at) }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="empty-state">暂无测评记录。前台跑通一次测评后，这里会出现会话数据。</p>
        </article>

        <article class="page-section">
          <div class="section-heading compact-heading">
            <h2>阶段观察热度</h2>
            <span class="muted">对话与 Agent 分布</span>
          </div>
          <div v-if="analytics?.stage_progress.length" class="stage-heat-list">
            <div
              v-for="item in analytics.stage_progress"
              :key="item.stage_title"
              class="stage-heat-item"
            >
              <div>
                <strong>{{ item.stage_title }}</strong>
                <span>
                  用户 {{ item.user_turn_count }} · AI {{ item.ai_turn_count }} · Trace
                  {{ item.trace_count }}
                </span>
              </div>
              <i
                :style="{
                  width: `${Math.min(
                    100,
                    Math.max(6, (item.user_turn_count + item.ai_turn_count) * 9),
                  )}%`,
                }"
              ></i>
            </div>
          </div>
          <p v-else class="empty-state">暂无阶段过程数据。</p>
        </article>
      </section>

      <section class="analytics-section">
        <div class="section-heading">
          <div>
            <p class="eyebrow">User Feedback</p>
            <h2>用户反馈与试测复盘</h2>
          </div>
          <span class="muted">用于判断情境、追问和报告是否需要继续调整。</span>
        </div>
        <div class="metric-grid analytics-metrics">
          <article v-for="item in feedbackMetrics" :key="item.label" class="metric metric-large">
            <span>{{ item.label }}</span>
            <strong>{{ item.value }}</strong>
            <small>{{ item.hint }}</small>
          </article>
        </div>
      </section>

      <section class="analytics-grid feedback-dashboard-grid">
        <article class="page-section">
          <div class="section-heading compact-heading">
            <h2>反馈维度均值</h2>
            <span class="muted">1-5 分，越高代表该项感受越强</span>
          </div>
          <div v-if="hasFeedbackData" class="feedback-score-list">
            <div v-for="item in feedbackScoreRows" :key="item.label" class="feedback-score-row">
              <div>
                <strong>{{ item.label }}</strong>
                <span>{{ item.hint }}</span>
              </div>
              <b>{{ formatScore(item.value) }}</b>
              <i><em :style="{ width: scoreBarWidth(item.value) }"></em></i>
            </div>
          </div>
          <p v-else class="empty-state">暂无用户反馈。完成测评后在报告页提交反馈，这里会自动汇总。</p>
        </article>

        <article class="page-section">
          <div class="section-heading compact-heading">
            <h2>最近开放反馈</h2>
            <span class="muted">用于定位体验问题</span>
          </div>
          <div v-if="analytics?.recent_feedback_comments.length" class="feedback-comment-list">
            <article
              v-for="item in analytics.recent_feedback_comments"
              :key="`${item.nickname}-${item.submitted_at}`"
              class="feedback-comment"
            >
              <div>
                <strong>{{ item.nickname }}</strong>
                <span>{{ formatDate(item.submitted_at) }}</span>
              </div>
              <p>{{ item.open_feedback }}</p>
              <small>
                满意度 {{ item.overall_satisfaction_score }} · 追问自然度
                {{ item.naturalness_score }} · 报告可信度 {{ item.report_trust_score }}
              </small>
            </article>
          </div>
          <p v-else class="empty-state">暂时没有开放文本反馈。</p>
        </article>
      </section>

      <section class="analytics-section">
        <div class="section-heading">
          <div>
            <p class="eyebrow">Configuration Assets</p>
            <h2>测评配置资产</h2>
          </div>
          <span class="muted">这些配置会进入 Agent 上下文和评分依据。</span>
        </div>
        <div class="config-strip">
          <article v-for="[label, key, activeKey] in configMetrics" :key="key" class="config-card">
            <span>{{ label }}</span>
            <strong>{{ metricValue(key) }}</strong>
            <small v-if="activeKey">启用 {{ metricValue(activeKey) }}</small>
            <small v-else>已配置</small>
          </article>
        </div>
      </section>

      <section v-if="!hasRuntimeData" class="notice">
        现在数据库里还没有测评会话。可以先用前台完成一次测评，或运行后端测试脚本生成模拟会话，再回到这里查看运行数据。
      </section>
    </template>
  </section>
</template>

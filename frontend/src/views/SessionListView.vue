<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { api } from "../api/client";
import type {
  AdminSessionListResponse,
  ExpertScoreBatchResponse,
  HumanReviewStatus,
} from "../types/admin";

const result = ref<AdminSessionListResponse>({ items: [], total: 0, page: 1, page_size: 20 });
const loading = ref(false);
const exporting = ref(false);
const importing = ref(false);
const error = ref("");
const search = ref("");
const status = ref("");
const scenarioCode = ref("");
const reviewStatus = ref<HumanReviewStatus | "">("");
const lowConfidence = ref(false);
const importInput = ref<HTMLInputElement | null>(null);
const importMessage = ref("");
const page = ref(1);
const pageSize = 20;

const totalPages = computed(() => Math.max(1, Math.ceil(result.value.total / pageSize)));

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const { data } = await api.get<AdminSessionListResponse>("/admin/sessions", {
      params: {
        status: status.value || undefined,
        scenario_code: scenarioCode.value.trim() || undefined,
        search: search.value.trim() || undefined,
        review_status: reviewStatus.value || undefined,
        low_confidence: lowConfidence.value || undefined,
        confidence_threshold: 0.5,
        page: page.value,
        page_size: pageSize,
      },
    });
    result.value = data;
  } catch {
    error.value = "会话列表读取失败，请确认后端和数据库已经启动。";
  } finally {
    loading.value = false;
  }
}

async function applyFilters() {
  page.value = 1;
  await load();
}

async function changePage(nextPage: number) {
  if (nextPage < 1 || nextPage > totalPages.value || nextPage === page.value) return;
  page.value = nextPage;
  await load();
}

async function exportData(format: "json" | "csv_zip") {
  exporting.value = true;
  error.value = "";
  try {
    const response = await api.get<Blob>("/admin/sessions/export", {
      params: {
        format,
        status: status.value || "completed",
        scenario_code: scenarioCode.value.trim() || undefined,
        search: search.value.trim() || undefined,
        review_status: reviewStatus.value || undefined,
        low_confidence: lowConfidence.value || undefined,
        confidence_threshold: 0.5,
      },
      responseType: "blob",
    });
    const extension = format === "json" ? "json" : "zip";
    const url = URL.createObjectURL(response.data);
    const link = document.createElement("a");
    link.href = url;
    link.download = `assessment-research-export-${new Date().toISOString().slice(0, 10)}.${extension}`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  } catch {
    error.value = "研究数据导出失败，请稍后重试。";
  } finally {
    exporting.value = false;
  }
}

function downloadImportTemplate() {
  const header = [
    "session_uuid",
    "stage_code",
    "dimension_key",
    "assessment_status",
    "score",
    "evidence_ids",
    "bars_reason",
    "next_level_gap",
    "annotator_confidence",
    "review_flag",
    "review_reason",
  ].join(",");
  const example = [
    "替换为会话UUID",
    "stage_1",
    "evidence_analysis",
    "scored",
    "3",
    "",
    "与三级行为锚点基本一致",
    "尚未比较备选解释",
    "medium",
    "false",
    "",
  ].join(",");
  downloadText(`\ufeff${header}\n${example}\n`, "expert-score-import-template.csv");
}

function downloadText(content: string, filename: string) {
  const url = URL.createObjectURL(new Blob([content], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function importExpertScores(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  importing.value = true;
  error.value = "";
  importMessage.value = "";
  try {
    const csv = await file.text();
    const { data } = await api.post<ExpertScoreBatchResponse>(
      "/admin/expert-scores/import",
      csv,
      { headers: { "Content-Type": "text/csv" } },
    );
    importMessage.value = `已导入 ${data.imported_count} 条专家评分，批次 ${data.import_batch_id || "-"}`;
    await load();
  } catch (reason) {
    const detail = (
      reason as { response?: { data?: { detail?: { message?: string } | string } } }
    ).response?.data?.detail;
    error.value =
      typeof detail === "string"
        ? detail
        : detail?.message || "专家评分导入失败，请检查 CSV 字段和数据。";
  } finally {
    importing.value = false;
    input.value = "";
  }
}

function formatDate(value: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "-" : date.toLocaleString("zh-CN", { hour12: false });
}

function formatConfidence(value: number | null) {
  return value == null ? "-" : value.toFixed(2);
}

function reviewStatusLabel(value: HumanReviewStatus) {
  return {
    pending: "待复核",
    in_review: "复核中",
    completed: "已完成",
    needs_adjudication: "需裁决",
  }[value];
}

onMounted(load);
</script>

<template>
  <section class="page-stack session-list-page">
    <div class="analytics-hero">
      <div>
        <p class="eyebrow">Session Review</p>
        <h2>测评会话复盘</h2>
        <p>按会话查看完整对话、Agent 决策、评分证据、报告和用户反馈。</p>
      </div>
      <div class="session-export-actions">
        <input
          ref="importInput"
          class="sr-only"
          type="file"
          accept=".csv,text/csv"
          @change="importExpertScores"
        />
        <button class="ghost-button" type="button" @click="downloadImportTemplate">
          下载评分模板
        </button>
        <button
          class="ghost-button"
          type="button"
          :disabled="importing"
          @click="importInput?.click()"
        >
          {{ importing ? "导入中" : "导入专家评分" }}
        </button>
        <button class="ghost-button" type="button" :disabled="exporting" @click="exportData('json')">
          导出 JSON
        </button>
        <button class="ghost-button" type="button" :disabled="exporting" @click="exportData('csv_zip')">
          导出 CSV ZIP
        </button>
      </div>
    </div>

    <section class="page-section session-filter-panel">
      <form class="session-filter-grid" @submit.prevent="applyFilters">
        <label>
          <span>搜索</span>
          <input v-model="search" placeholder="昵称或 Session UUID" />
        </label>
        <label>
          <span>状态</span>
          <select v-model="status">
            <option value="">全部状态</option>
            <option value="created">created</option>
            <option value="in_progress">in_progress</option>
            <option value="generating">generating</option>
            <option value="completed">completed</option>
            <option value="abandoned">abandoned</option>
          </select>
        </label>
        <label>
          <span>情境代码</span>
          <input v-model="scenarioCode" placeholder="product_launch_48h" />
        </label>
        <label>
          <span>复核状态</span>
          <select v-model="reviewStatus">
            <option value="">全部复核状态</option>
            <option value="pending">待复核</option>
            <option value="in_review">复核中</option>
            <option value="completed">已完成</option>
            <option value="needs_adjudication">需裁决</option>
          </select>
        </label>
        <label class="filter-checkbox">
          <input v-model="lowConfidence" type="checkbox" />
          <span>仅看低置信度（&lt; 0.5）</span>
        </label>
        <button class="assessment-primary compact-action" type="submit" :disabled="loading">
          {{ loading ? "查询中" : "查询" }}
        </button>
      </form>
      <p class="privacy-note">
        导出内容会移除直接身份字段，但自由文本可能包含参与者主动输入的信息，应按研究数据安全管理。
      </p>
    </section>

    <p v-if="error" class="assessment-error">{{ error }}</p>
    <p v-if="importMessage" class="assessment-success">{{ importMessage }}</p>

    <section class="page-section session-table-panel">
      <div class="section-heading compact-heading">
        <h2>会话记录</h2>
        <span class="muted">共 {{ result.total }} 条</span>
      </div>
      <div v-if="loading" class="empty-state">正在读取会话...</div>
      <div v-else-if="result.items.length" class="table-scroll">
        <table class="data-table session-table">
          <thead>
            <tr>
              <th>参与者</th>
              <th>情境</th>
              <th>状态</th>
              <th>过程数据</th>
              <th>报告</th>
              <th>AI 最低置信度</th>
              <th>专家评分</th>
              <th>复核状态</th>
              <th>耗时</th>
              <th>更新时间</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in result.items" :key="item.session_uuid">
              <td>
                <strong>{{ item.nickname }}</strong>
                <small>{{ item.session_uuid.slice(0, 8) }}</small>
              </td>
              <td>{{ item.scenario_title }}</td>
              <td><span class="status-pill">{{ item.status }}</span></td>
              <td>{{ item.turn_count }} 轮 · {{ item.agent_trace_count }} Trace</td>
              <td>{{ item.report_status || "未生成" }}</td>
              <td>
                <span :class="{ 'confidence-low': item.min_ai_confidence != null && item.min_ai_confidence < 0.5 }">
                  {{ formatConfidence(item.min_ai_confidence) }}
                </span>
              </td>
              <td>
                {{ item.expert_score_count }}/{{ item.expert_score_target_count }}
                <small>{{ item.expert_score_completion_rate }}%</small>
              </td>
              <td>
                <span class="status-pill">{{ reviewStatusLabel(item.review_status) }}</span>
              </td>
              <td>{{ item.duration_minutes == null ? "-" : `${item.duration_minutes} 分钟` }}</td>
              <td>{{ formatDate(item.updated_at) }}</td>
              <td>
                <RouterLink class="table-link" :to="`/admin/sessions/${item.session_uuid}`">
                  查看复盘
                </RouterLink>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="empty-state">没有符合当前条件的测评会话。</p>

      <div class="pagination-bar">
        <button class="ghost-button" type="button" :disabled="page <= 1" @click="changePage(page - 1)">
          上一页
        </button>
        <span>第 {{ page }} / {{ totalPages }} 页</span>
        <button
          class="ghost-button"
          type="button"
          :disabled="page >= totalPages"
          @click="changePage(page + 1)"
        >
          下一页
        </button>
      </div>
    </section>
  </section>
</template>

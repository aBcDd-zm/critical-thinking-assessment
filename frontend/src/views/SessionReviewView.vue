<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";
import { api } from "../api/client";
import type {
  AdminSessionReviewResponse,
  ExpertScore,
  ExpertScoreBatchResponse,
  ExpertScoreWrite,
  HumanReviewDecision,
  HumanReviewStatus,
} from "../types/admin";

interface ExpertScoreDraft {
  assessment_status: "" | "scored" | "insufficient_evidence";
  score: number | null;
  evidence_ids: string;
  bars_reason: string;
  next_level_gap: string;
  annotator_confidence: "high" | "medium" | "low";
  review_flag: boolean;
  review_reason: string;
}

const route = useRoute();
const sessionUuid = computed(() => String(route.params.sessionUuid || ""));
const review = ref<AdminSessionReviewResponse | null>(null);
const loading = ref(false);
const reviewSaving = ref(false);
const expertSaving = ref(false);
const error = ref("");
const saveMessage = ref("");
const reviewForm = ref<{
  status: HumanReviewStatus;
  decision: HumanReviewDecision | "";
  notes: string;
}>({
  status: "pending",
  decision: "",
  notes: "",
});
const expertDrafts = ref<Record<string, ExpertScoreDraft>>({});

const currentExpertScoreCount = computed(
  () => review.value?.expert_scores.filter((item) => item.is_current_annotator).length ?? 0,
);
const otherExpertScores = computed(
  () => review.value?.expert_scores.filter((item) => !item.is_current_annotator) ?? [],
);
const profileTurns = computed(
  () => review.value?.turns.filter((turn) => turn.content_type.startsWith("profile_")) ?? [],
);
const formalTurns = computed(
  () => review.value?.turns.filter((turn) => !turn.content_type.startsWith("profile_")) ?? [],
);

async function load() {
  if (!sessionUuid.value) return;
  loading.value = true;
  error.value = "";
  try {
    const { data } = await api.get<AdminSessionReviewResponse>(
      `/admin/sessions/${sessionUuid.value}/review`,
    );
    review.value = data;
    initializeReviewForms(data);
  } catch {
    review.value = null;
    error.value = "会话复盘读取失败，该会话可能不存在或后端尚未启动。";
  } finally {
    loading.value = false;
  }
}

function targetKey(stageCode: string, dimensionKey: string) {
  return `${stageCode}::${dimensionKey}`;
}

function initializeReviewForms(data: AdminSessionReviewResponse) {
  reviewForm.value = {
    status: data.human_review.status,
    decision: data.human_review.decision || "",
    notes: data.human_review.notes || "",
  };
  const currentScores = new Map<string, ExpertScore>(
    data.expert_scores
      .filter((item) => item.is_current_annotator)
      .map((item) => [targetKey(item.stage_code, item.dimension_key), item]),
  );
  const drafts: Record<string, ExpertScoreDraft> = {};
  for (const target of data.expert_score_targets) {
    const existing = currentScores.get(targetKey(target.stage_code, target.dimension_key));
    drafts[targetKey(target.stage_code, target.dimension_key)] = {
      assessment_status: existing?.assessment_status || "",
      score: existing?.score ?? null,
      evidence_ids: existing?.evidence_ids.join("|") || "",
      bars_reason: existing?.bars_reason || "",
      next_level_gap: existing?.next_level_gap || "",
      annotator_confidence: existing?.annotator_confidence || "medium",
      review_flag: existing?.review_flag || false,
      review_reason: existing?.review_reason || "",
    };
  }
  expertDrafts.value = drafts;
}

function changeAssessmentStatus(draft: ExpertScoreDraft) {
  if (draft.assessment_status === "insufficient_evidence") {
    draft.score = null;
  }
}

function parseEvidenceIds(value: string) {
  if (!value.trim()) return [];
  return value
    .replaceAll(";", "|")
    .replaceAll(",", "|")
    .split("|")
    .map((item) => Number(item.trim()))
    .filter((item) => Number.isInteger(item) && item > 0);
}

async function saveHumanReview() {
  if (!review.value) return;
  reviewSaving.value = true;
  error.value = "";
  saveMessage.value = "";
  try {
    await api.put(`/admin/sessions/${sessionUuid.value}/human-review`, {
      status: reviewForm.value.status,
      decision: reviewForm.value.decision || null,
      notes: reviewForm.value.notes.trim() || null,
    });
    saveMessage.value = "人工复核状态已保存。";
    await load();
  } catch (reason) {
    error.value = apiErrorMessage(reason, "人工复核保存失败，请检查复核状态和结论。");
  } finally {
    reviewSaving.value = false;
  }
}

async function saveExpertScores() {
  if (!review.value) return;
  const items: ExpertScoreWrite[] = [];
  for (const target of review.value.expert_score_targets) {
    const draft = expertDrafts.value[targetKey(target.stage_code, target.dimension_key)];
    if (!draft || !draft.assessment_status) continue;
    items.push({
      stage_code: target.stage_code,
      dimension_key: target.dimension_key,
      assessment_status: draft.assessment_status,
      score: draft.assessment_status === "scored" ? draft.score : null,
      evidence_ids: parseEvidenceIds(draft.evidence_ids),
      bars_reason: draft.bars_reason.trim(),
      next_level_gap: draft.next_level_gap.trim() || null,
      annotator_confidence: draft.annotator_confidence,
      review_flag: draft.review_flag,
      review_reason: draft.review_reason.trim() || null,
    });
  }
  if (!items.length) {
    error.value = "请至少完成一个阶段维度的专家评分。";
    return;
  }
  expertSaving.value = true;
  error.value = "";
  saveMessage.value = "";
  try {
    const { data } = await api.put<ExpertScoreBatchResponse>(
      `/admin/sessions/${sessionUuid.value}/expert-scores`,
      { items },
    );
    saveMessage.value = `已保存 ${data.saved_count} 条专家评分。`;
    await load();
  } catch (reason) {
    error.value = apiErrorMessage(reason, "专家评分保存失败，请检查必填项和证据 ID。");
  } finally {
    expertSaving.value = false;
  }
}

function apiErrorMessage(reason: unknown, fallback: string) {
  const detail = (
    reason as {
      response?: {
        data?: {
          detail?:
            | string
            | Array<string | { msg?: string; message?: string }>
            | { message?: string };
        };
      };
    }
  ).response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) =>
        typeof item === "string" ? item : item.msg || item.message || "数据校验失败",
      )
      .join("；");
  }
  return detail?.message || fallback;
}

function formatDate(value: string | null | undefined) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "-" : date.toLocaleString("zh-CN", { hour12: false });
}

function pretty(value: unknown) {
  return JSON.stringify(value, null, 2);
}

onMounted(load);
watch(() => route.params.sessionUuid, load);
</script>

<template>
  <section class="page-stack session-review-page">
    <div class="review-toolbar">
      <RouterLink class="table-link" to="/admin/sessions">← 返回会话列表</RouterLink>
      <button class="ghost-button" type="button" :disabled="loading" @click="load">刷新</button>
    </div>

    <p v-if="error" class="assessment-error">{{ error }}</p>
    <p v-if="saveMessage" class="assessment-success">{{ saveMessage }}</p>
    <div v-if="loading" class="page-section empty-state">正在读取完整会话证据链...</div>

    <template v-else-if="review">
      <section class="analytics-hero review-hero">
        <div>
          <p class="eyebrow">{{ review.session.scenario_code }}</p>
          <h2>{{ review.session.nickname }} · {{ review.session.scenario_title }}</h2>
          <p class="session-uuid-line">{{ review.session.session_uuid }}</p>
        </div>
        <div class="summary-badges">
          <span class="status-pill">{{ review.session.status }}</span>
          <span class="status-pill muted-pill">{{ review.session.assessment_mode }}</span>
          <span class="status-pill muted-pill">
            表达风格：{{ review.session.interviewer_style_version }}
          </span>
        </div>
      </section>

      <div class="review-summary-grid">
        <article class="metric metric-large">
          <span>职业背景</span>
          <strong>{{ review.session.occupation || "-" }}</strong>
          <small>{{ review.session.occupation_category || "未填写" }}</small>
        </article>
        <article class="metric metric-large">
          <span>情景来源</span>
          <strong>{{ review.session.scenario_source_type }}</strong>
          <small>
            {{ review.session.scenario_cache_hit ? "缓存命中" : "新生成" }} ·
            {{ review.session.scenario_generation_status || "历史情景" }}
          </small>
        </article>
        <article class="metric metric-large">
          <span>当前阶段</span>
          <strong>{{ review.session.flow_version.startsWith("progressive_v3") ? "渐进式访谈" : (review.session.current_stage_title || "-") }}</strong>
          <small>{{ review.session.flow_version }} · state {{ review.session.state_version }}</small>
        </article>
        <article class="metric metric-large">
          <span>对话轮次</span>
          <strong>{{ review.turns.length }}</strong>
          <small>{{ review.traces.length }} 条 Agent Trace</small>
        </article>
        <article class="metric metric-large">
          <span>测评耗时</span>
          <strong>{{ review.session.duration_minutes == null ? "-" : review.session.duration_minutes }}</strong>
          <small>{{ review.session.duration_minutes == null ? "尚未完成" : "分钟" }}</small>
        </article>
        <article class="metric metric-large">
          <span>更新时间</span>
          <strong class="metric-date">{{ formatDate(review.session.updated_at) }}</strong>
          <small>版本 {{ review.session.scenario_version }}</small>
        </article>
      </div>

      <section v-if="review.progressive_audit" class="page-section">
        <div class="section-heading compact-heading">
          <h2>V3 骨架、对话与证据审计</h2>
          <span class="muted">仅管理端可见：身份约束、任务域、单次话术、槽位、验证与 fallback</span>
        </div>
        <pre>{{ pretty(review.progressive_audit) }}</pre>
      </section>

      <section v-if="profileTurns.length" class="page-section">
        <div class="section-heading compact-heading">
          <h2>非评分背景访谈</h2>
          <span class="muted">仅用于情景适配，不进入六维评分、证据句或匿名导出</span>
        </div>
        <div class="review-timeline">
          <article
            v-for="turn in profileTurns"
            :key="turn.turn_id"
            class="review-turn"
            :class="`review-turn-${turn.speaker}`"
          >
            <div class="review-turn-meta">
              <strong>#{{ turn.turn_index }} · {{ turn.speaker }}</strong>
              <span>{{ turn.content_type }}</span>
              <time>{{ formatDate(turn.created_at) }}</time>
            </div>
            <p>{{ turn.content }}</p>
          </article>
        </div>
      </section>

      <section class="page-section human-review-panel">
        <div class="section-heading compact-heading">
          <div>
            <h2>人工复核结论</h2>
            <p class="muted">
              当前复核人：{{ review.human_review.reviewer_name || "尚未认领" }}
            </p>
          </div>
          <span class="status-pill">{{ review.human_review.status }}</span>
        </div>
        <div class="human-review-form">
          <label>
            <span>复核状态</span>
            <select v-model="reviewForm.status">
              <option value="pending">待复核</option>
              <option value="in_review">复核中</option>
              <option value="completed">已完成</option>
              <option value="needs_adjudication">需裁决</option>
            </select>
          </label>
          <label>
            <span>复核结论</span>
            <select v-model="reviewForm.decision">
              <option value="">暂不下结论</option>
              <option value="valid">有效</option>
              <option value="needs_adjudication">需要裁决</option>
              <option value="exclude">排除样本</option>
            </select>
          </label>
          <label class="review-notes-field">
            <span>复核备注</span>
            <textarea
              v-model="reviewForm.notes"
              rows="3"
              maxlength="5000"
              placeholder="记录复核依据、数据质量问题或后续处理要求"
            />
          </label>
          <button
            class="assessment-primary compact-action"
            type="button"
            :disabled="reviewSaving"
            @click="saveHumanReview"
          >
            {{ reviewSaving ? "保存中" : "保存复核结论" }}
          </button>
        </div>
      </section>

      <section class="page-section expert-score-panel">
        <div class="section-heading compact-heading">
          <div>
            <h2>专家独立评分</h2>
            <p class="muted">
              当前专家已完成 {{ currentExpertScoreCount }}/{{ review.expert_score_targets.length }}
            </p>
          </div>
          <button
            class="assessment-primary compact-action"
            type="button"
            :disabled="expertSaving"
            @click="saveExpertScores"
          >
            {{ expertSaving ? "保存中" : "保存专家评分" }}
          </button>
        </div>

        <div v-if="review.expert_score_targets.length" class="expert-score-editor">
          <article
            v-for="target in review.expert_score_targets"
            :key="targetKey(target.stage_code, target.dimension_key)"
            class="expert-score-edit-card"
          >
            <div class="expert-target-heading">
              <div>
                <strong>{{ target.stage_title }} · {{ target.dimension_name }}</strong>
                <small>{{ target.stage_code }} / {{ target.dimension_key }}</small>
              </div>
              <span>
                AI：{{ target.ai_score == null ? "IE/无" : target.ai_score }}
                · 置信度 {{ target.ai_confidence == null ? "-" : target.ai_confidence }}
              </span>
            </div>

            <template v-if="expertDrafts[targetKey(target.stage_code, target.dimension_key)]">
              <div class="expert-score-fields">
                <label>
                  <span>评分状态</span>
                  <select
                    v-model="expertDrafts[targetKey(target.stage_code, target.dimension_key)].assessment_status"
                    @change="changeAssessmentStatus(expertDrafts[targetKey(target.stage_code, target.dimension_key)])"
                  >
                    <option value="">尚未评分</option>
                    <option value="scored">1–5 分</option>
                    <option value="insufficient_evidence">IE（证据不足）</option>
                  </select>
                </label>
                <label>
                  <span>专家分数</span>
                  <select
                    v-model.number="expertDrafts[targetKey(target.stage_code, target.dimension_key)].score"
                    :disabled="
                      expertDrafts[targetKey(target.stage_code, target.dimension_key)].assessment_status !== 'scored'
                    "
                  >
                    <option :value="null">请选择</option>
                    <option v-for="score in 5" :key="score" :value="score">{{ score }}</option>
                  </select>
                </label>
                <label>
                  <span>专家置信度</span>
                  <select
                    v-model="expertDrafts[targetKey(target.stage_code, target.dimension_key)].annotator_confidence"
                  >
                    <option value="high">high</option>
                    <option value="medium">medium</option>
                    <option value="low">low</option>
                  </select>
                </label>
                <label>
                  <span>证据 ID</span>
                  <input
                    v-model="expertDrafts[targetKey(target.stage_code, target.dimension_key)].evidence_ids"
                    placeholder="例如 12|15"
                  />
                </label>
              </div>
              <label class="expert-text-field">
                <span>BARS 匹配理由</span>
                <textarea
                  v-model="expertDrafts[targetKey(target.stage_code, target.dimension_key)].bars_reason"
                  rows="2"
                  maxlength="5000"
                  placeholder="必填：说明与该等级行为锚点的匹配依据"
                />
              </label>
              <label class="expert-text-field">
                <span>下一等级差距</span>
                <textarea
                  v-model="expertDrafts[targetKey(target.stage_code, target.dimension_key)].next_level_gap"
                  rows="2"
                  maxlength="5000"
                  placeholder="未达到更高一级的实质缺口"
                />
              </label>
              <label class="expert-review-flag">
                <input
                  v-model="expertDrafts[targetKey(target.stage_code, target.dimension_key)].review_flag"
                  type="checkbox"
                />
                <span>建议进入裁决</span>
              </label>
              <label
                v-if="expertDrafts[targetKey(target.stage_code, target.dimension_key)].review_flag"
                class="expert-text-field"
              >
                <span>裁决原因</span>
                <textarea
                  v-model="expertDrafts[targetKey(target.stage_code, target.dimension_key)].review_reason"
                  rows="2"
                  maxlength="5000"
                  placeholder="必填：说明分歧、低置信度或异常原因"
                />
              </label>
            </template>
          </article>
        </div>
        <p v-else class="empty-state">该情境尚未配置阶段—维度评分目标。</p>

        <details v-if="otherExpertScores.length" class="trace-review-item other-expert-scores">
          <summary>查看其他专家的独立评分（{{ otherExpertScores.length }}）</summary>
          <div class="other-expert-score-list">
            <article v-for="score in otherExpertScores" :key="score.annotation_id">
              <strong>{{ score.annotator_name }} · {{ score.stage_title }} · {{ score.dimension_name }}</strong>
              <span>{{ score.score == null ? "IE" : `${score.score} 分` }}</span>
              <p>{{ score.bars_reason }}</p>
            </article>
          </div>
        </details>
      </section>

      <section class="page-section">
        <div class="section-heading compact-heading">
          <h2>完整对话时间线</h2>
          <span class="muted">用户实际看到和提交的文本</span>
        </div>
        <div class="review-timeline">
          <article
            v-for="turn in formalTurns"
            :key="turn.turn_id"
            class="review-turn"
            :class="`review-turn-${turn.speaker}`"
          >
            <div class="review-turn-meta">
              <strong>#{{ turn.turn_index }} · {{ turn.speaker }}</strong>
              <span>{{ turn.stage_title || "未绑定阶段" }}</span>
              <span>{{ turn.content_type }}</span>
              <span v-if="turn.intervention_rule_code">规则：{{ turn.intervention_rule_code }}</span>
              <span v-if="turn.dynamic_info_code">动态信息：{{ turn.dynamic_info_code }}</span>
              <time>{{ formatDate(turn.created_at) }}</time>
            </div>
            <p>{{ turn.content }}</p>
          </article>
        </div>
      </section>

      <section class="page-section">
        <div class="section-heading compact-heading">
          <h2>Agent 调用记录</h2>
          <span class="muted">只读原始输入、输出与错误信息</span>
        </div>
        <div v-if="review.traces.length" class="trace-review-list">
          <details v-for="trace in review.traces" :key="trace.trace_id" class="trace-review-item">
            <summary>
              <strong>{{ trace.agent_name }}</strong>
              <span>{{ trace.stage_title || "全局" }}</span>
              <span :class="['ok', 'success'].includes(trace.status) ? 'trace-ok' : 'trace-failed'">
                {{ trace.status }}
              </span>
              <span>{{ trace.duration_ms == null ? "-" : `${trace.duration_ms} ms` }}</span>
              <span v-if="trace.model_name">模型：{{ trace.model_name }}</span>
              <span v-if="trace.prompt_template_id">Prompt #{{ trace.prompt_template_id }}</span>
              <span v-if="trace.interviewer_style_version">
                风格：{{ trace.interviewer_style_version }}
              </span>
              <span v-if="trace.parent_trace_id">Planner Trace #{{ trace.parent_trace_id }}</span>
              <span v-if="trace.fallback_type">fallback：{{ trace.fallback_type }}</span>
              <span v-if="trace.fallback_reason">原因：{{ trace.fallback_reason }}</span>
              <span v-if="trace.selected_rule_code">{{ trace.selected_rule_code }}</span>
              <span v-if="trace.selected_dynamic_info_code">{{ trace.selected_dynamic_info_code }}</span>
            </summary>
            <p v-if="trace.validation_codes?.length" class="assessment-error">
              校验码：{{ trace.validation_codes.join("、") }}
            </p>
            <div class="trace-json-grid">
              <div v-if="trace.config_snapshot_json">
                <h3>Config</h3>
                <pre>{{ pretty(trace.config_snapshot_json) }}</pre>
              </div>
              <div>
                <h3>Input</h3>
                <pre>{{ pretty(trace.input_json) }}</pre>
              </div>
              <div>
                <h3>Output</h3>
                <pre>{{ pretty(trace.output_json) }}</pre>
              </div>
            </div>
            <div v-if="trace.raw_output" class="trace-raw-output">
              <h3>Raw Output</h3>
              <pre>{{ trace.raw_output }}</pre>
            </div>
            <p v-if="trace.error_code" class="assessment-error">{{ trace.error_code }}</p>
          </details>
        </div>
        <p v-else class="empty-state">暂无 Agent 调用记录。</p>
      </section>

      <section class="page-section">
        <div class="section-heading compact-heading">
          <h2>评分与证据</h2>
          <span class="muted">{{ review.score_snapshots.length }} 个快照</span>
        </div>
        <div v-if="review.score_snapshots.length" class="snapshot-list">
          <article v-for="snapshot in review.score_snapshots" :key="snapshot.snapshot_id" class="snapshot-card">
            <div class="snapshot-heading">
              <div>
                <strong>{{ snapshot.snapshot_type }}</strong>
                <span>{{ snapshot.stage_title || "最终评分" }}</span>
              </div>
              <time>{{ formatDate(snapshot.created_at) }}</time>
            </div>
            <p v-if="snapshot.summary">{{ snapshot.summary }}</p>
            <div class="review-score-grid">
              <article v-for="score in snapshot.results" :key="score.score_result_id" class="review-score-card">
                <div>
                  <span>{{ score.dimension_name }}</span>
                  <strong>{{ score.score == null ? "暂不评分" : score.score }}</strong>
                </div>
                <p>{{ score.reason }}</p>
                <small v-if="score.evidence_sufficiency_index != null">
                  证据基础指数（ESI）：{{ score.evidence_sufficiency_index }}/100
                  · {{ score.score_kind === "supported" ? "关键评分证据已达标" : "关键评分证据未达标" }}
                </small>
                <small v-else>Legacy 置信度：{{ score.confidence == null ? "-" : score.confidence }}</small>
                <blockquote v-for="evidence in score.evidence" :key="evidence.evidence_id">
                  “{{ evidence.evidence_text }}”
                  <small>{{ evidence.evidence_type }} · Turn {{ evidence.dialogue_turn_id || "-" }}</small>
                </blockquote>
              </article>
            </div>
          </article>
        </div>
        <p v-else class="empty-state">该会话尚未生成评分。</p>
      </section>

      <section class="analytics-grid review-bottom-grid">
        <article class="page-section">
          <div class="section-heading compact-heading">
            <h2>最终报告</h2>
            <span class="muted">{{ review.report?.status || "未生成" }}</span>
          </div>
          <template v-if="review.report">
            <p>{{ review.report.summary }}</p>
            <details class="trace-review-item">
              <summary>查看报告 JSON</summary>
              <pre>{{ pretty(review.report.report_json) }}</pre>
            </details>
          </template>
          <p v-else class="empty-state">暂无最终报告。</p>
        </article>

        <article class="page-section">
          <div class="section-heading compact-heading">
            <h2>用户反馈</h2>
            <span class="muted">{{ review.feedback ? formatDate(review.feedback.submitted_at) : "未提交" }}</span>
          </div>
          <div v-if="review.feedback" class="feedback-review-grid">
            <span>真实感 <strong>{{ review.feedback.realism_score }}</strong></span>
            <span>难度 <strong>{{ review.feedback.difficulty_score }}</strong></span>
            <span>自然度 <strong>{{ review.feedback.naturalness_score }}</strong></span>
            <span>疲劳感 <strong>{{ review.feedback.fatigue_score }}</strong></span>
            <span>报告可信度 <strong>{{ review.feedback.report_trust_score }}</strong></span>
            <span>总体满意度 <strong>{{ review.feedback.overall_satisfaction_score }}</strong></span>
            <p v-if="review.feedback.open_feedback">{{ review.feedback.open_feedback }}</p>
          </div>
          <p v-else class="empty-state">暂无用户反馈。</p>
        </article>
      </section>
    </template>
  </section>
</template>

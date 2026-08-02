<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { api } from "../api/client";
import type {
  Scenario,
  ScenarioListItem,
  ScenarioStage,
  StageDynamicInfo,
  StageInterventionRule,
} from "../types/admin";

const scenarios = ref<ScenarioListItem[]>([]);
const stages = ref<ScenarioStage[]>([]);
const dynamicInfos = ref<StageDynamicInfo[]>([]);
const rules = ref<StageInterventionRule[]>([]);
const selectedScenarioId = ref<number | null>(null);
const selectedStageId = ref<number | null>(null);
const loading = ref(false);
const saving = ref(false);
const message = ref("");

const selectedScenario = computed(
  () => scenarios.value.find((item) => item.id === selectedScenarioId.value) || null,
);
const selectedStage = computed(
  () => stages.value.find((item) => item.id === selectedStageId.value) || null,
);
const occupationBaseScenarios = computed(() =>
  scenarios.value.filter((item) => item.source_type === "ai_base"),
);
const otherScenarios = computed(() =>
  scenarios.value.filter((item) => item.source_type !== "ai_base"),
);

const scenarioForm = reactive({
  title: "",
  background: "",
  target_audience: "",
  scenario_type: "",
  difficulty_level: "",
  estimated_minutes: 30,
  rotation_weight: 1,
  is_default: false,
  version: "v1",
  status: "active",
});

const stageForm = reactive({
  title: "",
  stage_goal: "",
  context: "",
  main_question: "",
  context_generation_mode: "config_guided",
  context_ai_weight: 30,
  max_followups: 2,
  estimated_minutes: 5,
  status: "active",
});

const newDynamicInfo = reactive({
  info_code: "",
  title: "",
  content: "",
  info_type: "risk_signal",
  trigger_condition: "",
  priority: 100,
  status: "active",
});

const newRule = reactive({
  rule_code: "",
  rule_type: "open_followup",
  trigger_condition: "",
  strategy_direction: "",
  sample_question: "",
  question_generation_mode: "strategy_guided",
  question_ai_weight: 40,
  fallback_question: "",
  priority: 100,
  status: "active",
});

function fillScenarioForm(item: Scenario) {
  scenarioForm.title = item.title;
  scenarioForm.background = item.background;
  scenarioForm.target_audience = item.target_audience;
  scenarioForm.scenario_type = item.scenario_type;
  scenarioForm.difficulty_level = item.difficulty_level;
  scenarioForm.estimated_minutes = item.estimated_minutes;
  scenarioForm.rotation_weight = item.rotation_weight;
  scenarioForm.is_default = item.is_default;
  scenarioForm.version = item.version;
  scenarioForm.status = item.status;
}

function fillStageForm(item: ScenarioStage) {
  stageForm.title = item.title;
  stageForm.stage_goal = item.stage_goal;
  stageForm.context = item.context;
  stageForm.main_question = item.main_question;
  stageForm.context_generation_mode = item.context_generation_mode;
  stageForm.context_ai_weight = item.context_ai_weight;
  stageForm.max_followups = item.max_followups;
  stageForm.estimated_minutes = item.estimated_minutes;
  stageForm.status = item.status;
}

async function loadScenarios() {
  loading.value = true;
  try {
    const { data } = await api.get<ScenarioListItem[]>("/admin/scenarios");
    scenarios.value = data;
    if (data.length && !selectedScenarioId.value) {
      await selectScenario(data[0].id);
    }
  } finally {
    loading.value = false;
  }
}

async function selectScenario(id: number) {
  selectedScenarioId.value = id;
  selectedStageId.value = null;
  message.value = "";
  const [{ data: detail }, { data: stageData }] = await Promise.all([
    api.get<Scenario>(`/admin/scenarios/${id}`),
    api.get<ScenarioStage[]>(`/admin/scenarios/${id}/stages`),
  ]);
  fillScenarioForm(detail);
  stages.value = stageData;
  if (stageData.length) {
    await selectStage(stageData[0].id);
  } else {
    dynamicInfos.value = [];
    rules.value = [];
  }
}

async function selectStage(id: number) {
  selectedStageId.value = id;
  const stage = stages.value.find((item) => item.id === id);
  if (stage) fillStageForm(stage);
  const [{ data: infoData }, { data: ruleData }] = await Promise.all([
    api.get<StageDynamicInfo[]>(`/admin/stages/${id}/dynamic-infos`),
    api.get<StageInterventionRule[]>(`/admin/stages/${id}/intervention-rules`),
  ]);
  dynamicInfos.value = infoData;
  rules.value = ruleData;
}

async function saveScenario() {
  if (!selectedScenarioId.value) return;
  saving.value = true;
  message.value = "";
  try {
    const payload = selectedScenario.value?.is_immutable
      ? { status: scenarioForm.status }
      : scenarioForm;
    await api.put<Scenario>(`/admin/scenarios/${selectedScenarioId.value}`, payload);
    await loadScenarios();
    message.value = "情境配置已保存。";
  } finally {
    saving.value = false;
  }
}

async function regenerateScenario() {
  if (!selectedScenarioId.value || selectedScenario.value?.source_type !== "ai_base") return;
  saving.value = true;
  message.value = "正在重新生成并审查职业基础情景...";
  try {
    const { data } = await api.post<Scenario>(
      `/admin/scenarios/${selectedScenarioId.value}/regenerate`,
    );
    await loadScenarios();
    await selectScenario(data.id);
    message.value = "新版本已生成，旧版本已停用。";
  } finally {
    saving.value = false;
  }
}

async function saveStage() {
  if (!selectedStageId.value) return;
  saving.value = true;
  message.value = "";
  try {
    const { data } = await api.put<ScenarioStage>(
      `/admin/stages/${selectedStageId.value}`,
      stageForm,
    );
    const index = stages.value.findIndex((item) => item.id === data.id);
    if (index >= 0) stages.value[index] = data;
    fillStageForm(data);
    message.value = "阶段配置已保存。";
  } finally {
    saving.value = false;
  }
}

async function saveDynamicInfo(item: StageDynamicInfo) {
  saving.value = true;
  message.value = "";
  try {
    const { data } = await api.put<StageDynamicInfo>(`/admin/dynamic-infos/${item.id}`, {
      title: item.title,
      content: item.content,
      info_type: item.info_type,
      trigger_condition: item.trigger_condition,
      priority: item.priority,
      status: item.status,
    });
    const index = dynamicInfos.value.findIndex((current) => current.id === data.id);
    if (index >= 0) dynamicInfos.value[index] = data;
    message.value = "动态信息已保存。";
  } finally {
    saving.value = false;
  }
}

async function createDynamicInfo() {
  if (!selectedStageId.value) return;
  saving.value = true;
  message.value = "";
  try {
    const { data } = await api.post<StageDynamicInfo>(
      `/admin/stages/${selectedStageId.value}/dynamic-infos`,
      {
        ...newDynamicInfo,
        trigger_condition: newDynamicInfo.trigger_condition || null,
      },
    );
    dynamicInfos.value.push(data);
    Object.assign(newDynamicInfo, {
      info_code: "",
      title: "",
      content: "",
      info_type: "risk_signal",
      trigger_condition: "",
      priority: 100,
      status: "active",
    });
    message.value = "动态信息已新增。";
  } finally {
    saving.value = false;
  }
}

async function saveRule(item: StageInterventionRule) {
  saving.value = true;
  message.value = "";
  try {
    const { data } = await api.put<StageInterventionRule>(`/admin/intervention-rules/${item.id}`, {
      rule_type: item.rule_type,
      trigger_condition: item.trigger_condition,
      strategy_direction: item.strategy_direction,
      sample_question: item.sample_question,
      question_generation_mode: item.question_generation_mode,
      question_ai_weight: item.question_ai_weight,
      fallback_question: item.fallback_question,
      priority: item.priority,
      status: item.status,
    });
    const index = rules.value.findIndex((current) => current.id === data.id);
    if (index >= 0) rules.value[index] = data;
    message.value = "追问策略已保存。";
  } finally {
    saving.value = false;
  }
}

async function createRule() {
  if (!selectedStageId.value) return;
  saving.value = true;
  message.value = "";
  try {
    const { data } = await api.post<StageInterventionRule>(
      `/admin/stages/${selectedStageId.value}/intervention-rules`,
      {
        ...newRule,
        trigger_condition: newRule.trigger_condition || null,
        sample_question: newRule.sample_question || null,
        fallback_question: newRule.fallback_question || null,
      },
    );
    rules.value.push(data);
    Object.assign(newRule, {
      rule_code: "",
      rule_type: "open_followup",
      trigger_condition: "",
      strategy_direction: "",
      sample_question: "",
      question_generation_mode: "strategy_guided",
      question_ai_weight: 40,
      fallback_question: "",
      priority: 100,
      status: "active",
    });
    message.value = "追问策略已新增。";
  } finally {
    saving.value = false;
  }
}

async function exportYaml() {
  const { data } = await api.get<string>("/admin/seeds/export", {
    responseType: "text",
  });
  const blob = new Blob([data], { type: "application/x-yaml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `assessment-config-${new Date().toISOString().slice(0, 10)}.yaml`;
  link.click();
  URL.revokeObjectURL(url);
}

onMounted(loadScenarios);
</script>

<template>
  <section class="split-layout">
    <aside class="panel">
      <h2>测评情境</h2>
      <div v-if="loading" class="muted">正在加载...</div>
      <div v-else class="list">
        <h3 v-if="occupationBaseScenarios.length">职业基础情景</h3>
        <button
          v-for="item in occupationBaseScenarios"
          :key="item.id"
          class="list-item"
          :class="{ active: item.id === selectedScenarioId }"
          type="button"
          @click="selectScenario(item.id)"
        >
          <strong>{{ item.title }}</strong>
          <span>
            {{ item.scenario_code }} · {{ item.stage_count }} 个阶段 ·
            {{ item.source_type }}
          </span>
          <small v-if="item.occupation_category">
            {{ item.occupation_category }} · {{ item.occupation || "未记录具体职业" }} ·
            {{ item.generation_model || "未记录模型" }}
          </small>
          <small>
            Prompt {{ item.generation_prompt_version || "未记录" }} · 校验
            {{ item.validation_status || "未知" }}
          </small>
          <small>
            使用 {{ item.usage_count }} 次 · 最后使用
            {{ item.last_used_at ? new Date(item.last_used_at).toLocaleString() : "暂无" }}
          </small>
        </button>
        <h3 v-if="otherScenarios.length">通用与历史情景</h3>
        <button
          v-for="item in otherScenarios"
          :key="item.id"
          class="list-item"
          :class="{ active: item.id === selectedScenarioId }"
          type="button"
          @click="selectScenario(item.id)"
        >
          <strong>{{ item.title }}</strong>
          <span>
            {{ item.scenario_code }} · {{ item.stage_count }} 个阶段 ·
            {{ item.source_type }}
          </span>
          <small>使用 {{ item.usage_count }} 次</small>
        </button>
      </div>
      <div class="toolbar">
        <button class="ghost-button" type="button" @click="exportYaml">导出 YAML</button>
      </div>
    </aside>

    <main class="page-stack">
      <section v-if="selectedScenario" class="page-section">
        <h2>{{ selectedScenario.title }}</h2>
        <p v-if="selectedScenario.is_immutable" class="muted">
          这是不可变的 AI 生成情景。可停用或重新生成；历史会话继续引用原版本。
        </p>
        <div class="form-grid">
          <label class="field">
            <span>情境标题</span>
            <input v-model="scenarioForm.title" />
          </label>
          <label class="field">
            <span>适用对象</span>
            <input v-model="scenarioForm.target_audience" />
          </label>
          <label class="field">
            <span>情境类型</span>
            <input v-model="scenarioForm.scenario_type" />
          </label>
          <label class="field">
            <span>难度等级</span>
            <select v-model="scenarioForm.difficulty_level">
              <option value="easy">easy</option>
              <option value="medium">medium</option>
              <option value="hard">hard</option>
            </select>
          </label>
          <label class="field">
            <span>预计分钟数</span>
            <input v-model.number="scenarioForm.estimated_minutes" type="number" min="1" />
          </label>
          <label class="field">
            <span>轮换权重</span>
            <input v-model.number="scenarioForm.rotation_weight" type="number" min="0" />
          </label>
          <label class="field">
            <span>版本</span>
            <input v-model="scenarioForm.version" />
          </label>
          <label class="field">
            <span>状态</span>
            <select v-model="scenarioForm.status">
              <option value="active">active</option>
              <option value="draft">draft</option>
              <option value="disabled">disabled</option>
            </select>
          </label>
          <label class="field">
            <span>默认情境</span>
            <select v-model="scenarioForm.is_default">
              <option :value="true">是</option>
              <option :value="false">否</option>
            </select>
          </label>
          <label class="field full">
            <span>情境背景</span>
            <textarea v-model="scenarioForm.background" />
          </label>
        </div>
        <div class="toolbar">
          <span class="muted">{{ message }}</span>
          <button class="primary-button" type="button" :disabled="saving" @click="saveScenario">
            {{ selectedScenario.is_immutable ? "保存状态" : "保存情境" }}
          </button>
          <button
            v-if="selectedScenario.source_type === 'ai_base'"
            class="ghost-button"
            type="button"
            :disabled="saving"
            @click="regenerateScenario"
          >
            重新生成
          </button>
        </div>
      </section>

      <section v-if="stages.length" class="page-section">
        <h2>阶段配置</h2>
        <div class="list" style="margin-bottom: 14px">
          <button
            v-for="stage in stages"
            :key="stage.id"
            class="list-item"
            :class="{ active: stage.id === selectedStageId }"
            type="button"
            @click="selectStage(stage.id)"
          >
            <strong>{{ stage.stage_order }}. {{ stage.title }}</strong>
            <span>
              {{ stage.stage_code }} · AI 权重 {{ stage.context_ai_weight }} ·
              {{ stage.dimensions.map((item) => item.dimension_name).join("、") || "未绑定维度" }}
            </span>
          </button>
        </div>

        <div v-if="selectedStage" class="form-grid">
          <label class="field">
            <span>阶段标题</span>
            <input v-model="stageForm.title" />
          </label>
          <label class="field">
            <span>情境生成模式</span>
            <select v-model="stageForm.context_generation_mode">
              <option value="fixed_context">fixed_context</option>
              <option value="config_guided">config_guided</option>
              <option value="ai_expanded">ai_expanded</option>
            </select>
          </label>
          <label class="field">
            <span>情境 AI 生成权重：{{ stageForm.context_ai_weight }}</span>
            <input v-model.number="stageForm.context_ai_weight" type="range" min="0" max="100" />
          </label>
          <label class="field">
            <span>追问上限</span>
            <input v-model.number="stageForm.max_followups" type="number" min="0" />
          </label>
          <label class="field">
            <span>预计分钟数</span>
            <input v-model.number="stageForm.estimated_minutes" type="number" min="1" />
          </label>
          <label class="field">
            <span>状态</span>
            <select v-model="stageForm.status">
              <option value="active">active</option>
              <option value="draft">draft</option>
              <option value="disabled">disabled</option>
            </select>
          </label>
          <label class="field full">
            <span>阶段目标</span>
            <textarea v-model="stageForm.stage_goal" />
          </label>
          <label class="field full">
            <span>阶段上下文</span>
            <textarea v-model="stageForm.context" />
          </label>
          <label class="field full">
            <span>主问题</span>
            <textarea v-model="stageForm.main_question" />
          </label>
        </div>
        <div class="toolbar">
          <button class="primary-button" type="button" :disabled="saving || selectedScenario?.is_immutable" @click="saveStage">
            保存阶段
          </button>
        </div>
      </section>

      <section v-if="selectedStage" class="page-section">
        <h2>动态信息池</h2>
        <table class="data-table">
          <thead>
            <tr>
              <th style="width: 150px">标题</th>
              <th>内容</th>
              <th style="width: 130px">类型</th>
              <th style="width: 90px">优先级</th>
              <th style="width: 90px">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in dynamicInfos" :key="item.id">
              <td><input v-model="item.title" /></td>
              <td><textarea v-model="item.content" /></td>
              <td><input v-model="item.info_type" /></td>
              <td><input v-model.number="item.priority" type="number" min="0" /></td>
              <td>
                <button class="ghost-button" type="button" :disabled="selectedScenario?.is_immutable" @click="saveDynamicInfo(item)">
                  保存
                </button>
              </td>
            </tr>
          </tbody>
        </table>

        <h3 style="margin-top: 18px">新增动态信息</h3>
        <div class="form-grid">
          <label class="field">
            <span>编码</span>
            <input v-model="newDynamicInfo.info_code" placeholder="new_risk_signal" />
          </label>
          <label class="field">
            <span>标题</span>
            <input v-model="newDynamicInfo.title" />
          </label>
          <label class="field">
            <span>类型</span>
            <input v-model="newDynamicInfo.info_type" />
          </label>
          <label class="field">
            <span>优先级</span>
            <input v-model.number="newDynamicInfo.priority" type="number" min="0" />
          </label>
          <label class="field full">
            <span>内容</span>
            <textarea v-model="newDynamicInfo.content" />
          </label>
          <label class="field full">
            <span>触发条件</span>
            <textarea v-model="newDynamicInfo.trigger_condition" />
          </label>
        </div>
        <div class="toolbar">
          <button class="ghost-button" type="button" :disabled="saving || selectedScenario?.is_immutable" @click="createDynamicInfo">
            新增动态信息
          </button>
        </div>
      </section>

      <section v-if="selectedStage" class="page-section">
        <h2>追问策略</h2>
        <table class="data-table">
          <thead>
            <tr>
              <th style="width: 120px">类型</th>
              <th>策略方向</th>
              <th>示例问题</th>
              <th style="width: 120px">生成模式</th>
              <th style="width: 90px">AI 权重</th>
              <th style="width: 90px">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in rules" :key="item.id">
              <td><input v-model="item.rule_type" /></td>
              <td><textarea v-model="item.strategy_direction" /></td>
              <td><textarea v-model="item.sample_question" /></td>
              <td><input v-model="item.question_generation_mode" /></td>
              <td><input v-model.number="item.question_ai_weight" type="number" min="0" max="100" /></td>
              <td>
                <button class="ghost-button" type="button" :disabled="selectedScenario?.is_immutable" @click="saveRule(item)">
                  保存
                </button>
              </td>
            </tr>
          </tbody>
        </table>

        <h3 style="margin-top: 18px">新增追问策略</h3>
        <div class="form-grid">
          <label class="field">
            <span>编码</span>
            <input v-model="newRule.rule_code" placeholder="clarify_evidence_source" />
          </label>
          <label class="field">
            <span>规则类型</span>
            <select v-model="newRule.rule_type">
              <option value="open_followup">open_followup</option>
              <option value="clarify">clarify</option>
              <option value="challenge">challenge</option>
              <option value="trap">trap</option>
              <option value="dynamic_update">dynamic_update</option>
              <option value="advance">advance</option>
            </select>
          </label>
          <label class="field">
            <span>生成模式</span>
            <select v-model="newRule.question_generation_mode">
              <option value="fixed_question">fixed_question</option>
              <option value="template_guided">template_guided</option>
              <option value="strategy_guided">strategy_guided</option>
              <option value="ai_open">ai_open</option>
            </select>
          </label>
          <label class="field">
            <span>AI 权重：{{ newRule.question_ai_weight }}</span>
            <input v-model.number="newRule.question_ai_weight" type="range" min="0" max="100" />
          </label>
          <label class="field full">
            <span>触发条件</span>
            <textarea v-model="newRule.trigger_condition" />
          </label>
          <label class="field full">
            <span>策略方向</span>
            <textarea v-model="newRule.strategy_direction" />
          </label>
          <label class="field full">
            <span>示例问题</span>
            <textarea v-model="newRule.sample_question" />
          </label>
          <label class="field full">
            <span>兜底问题</span>
            <textarea v-model="newRule.fallback_question" />
          </label>
        </div>
        <div class="toolbar">
          <button class="ghost-button" type="button" :disabled="saving || selectedScenario?.is_immutable" @click="createRule">
            新增追问策略
          </button>
        </div>
      </section>
    </main>
  </section>
</template>

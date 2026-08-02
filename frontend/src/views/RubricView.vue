<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { api } from "../api/client";
import type { RubricAnchor, RubricDimension } from "../types/admin";

const dimensions = ref<RubricDimension[]>([]);
const selectedId = ref<number | null>(null);
const loading = ref(false);
const saving = ref(false);
const message = ref("");

const selected = computed(
  () => dimensions.value.find((item) => item.id === selectedId.value) || null,
);

const form = reactive({
  name: "",
  definition: "",
  observableText: "",
  invalid_evidence_desc: "",
  version: "v1",
  status: "active",
});

const anchorForms = ref<RubricAnchor[]>([]);

function fillForm(item: RubricDimension) {
  form.name = item.name;
  form.definition = item.definition;
  form.observableText = Array.isArray(item.observable_behaviors)
    ? item.observable_behaviors.join("\n")
    : JSON.stringify(item.observable_behaviors, null, 2);
  form.invalid_evidence_desc = item.invalid_evidence_desc || "";
  form.version = item.version;
  form.status = item.status;
  anchorForms.value = item.anchors.map((anchor) => ({
    ...anchor,
    evidence_examples: anchor.evidence_examples ? [...anchor.evidence_examples] : [],
    counter_examples: anchor.counter_examples ? [...anchor.counter_examples] : [],
  }));
}

function selectDimension(item: RubricDimension) {
  selectedId.value = item.id;
  fillForm(item);
  message.value = "";
}

async function load() {
  loading.value = true;
  try {
    const { data } = await api.get<RubricDimension[]>("/admin/rubric-dimensions");
    dimensions.value = data;
    if (data.length) {
      selectDimension(data[0]);
    }
  } finally {
    loading.value = false;
  }
}

async function saveDimension() {
  if (!selected.value) return;
  saving.value = true;
  message.value = "";
  try {
    const observable_behaviors = form.observableText
      .split("\n")
      .map((item) => item.trim())
      .filter(Boolean);
    const { data } = await api.put<RubricDimension>(
      `/admin/rubric-dimensions/${selected.value.id}`,
      {
        name: form.name,
        definition: form.definition,
        observable_behaviors,
        invalid_evidence_desc: form.invalid_evidence_desc || null,
        version: form.version,
        status: form.status,
      },
    );
    const index = dimensions.value.findIndex((item) => item.id === data.id);
    if (index >= 0) dimensions.value[index] = data;
    fillForm(data);
    message.value = "能力维度已保存。";
  } finally {
    saving.value = false;
  }
}

async function saveAnchor(anchor: RubricAnchor) {
  saving.value = true;
  message.value = "";
  try {
    const { data } = await api.put<RubricAnchor>(`/admin/rubric-anchors/${anchor.id}`, {
      level_name: anchor.level_name,
      behavior_desc: anchor.behavior_desc,
      evidence_examples: anchor.evidence_examples || [],
      counter_examples: anchor.counter_examples || [],
      status: anchor.status,
    });
    const index = anchorForms.value.findIndex((item) => item.id === data.id);
    if (index >= 0) anchorForms.value[index] = data;
    const dimension = dimensions.value.find((item) => item.id === data.dimension_id);
    if (dimension) {
      const anchorIndex = dimension.anchors.findIndex((item) => item.id === data.id);
      if (anchorIndex >= 0) dimension.anchors[anchorIndex] = data;
    }
    message.value = `评分锚点 ${data.score_level} 分已保存。`;
  } finally {
    saving.value = false;
  }
}

function listToText(value: string[] | null) {
  return (value || []).join("\n");
}

function textToList(text: string) {
  return text
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}

onMounted(load);
</script>

<template>
  <section class="split-layout">
    <aside class="panel">
      <h2>能力维度</h2>
      <div v-if="loading" class="muted">正在加载...</div>
      <div v-else class="list">
        <button
          v-for="item in dimensions"
          :key="item.id"
          class="list-item"
          :class="{ active: item.id === selectedId }"
          type="button"
          @click="selectDimension(item)"
        >
          <strong>{{ item.name }}</strong>
          <span>{{ item.dimension_key }} · {{ item.version }} · {{ item.status }}</span>
        </button>
      </div>
    </aside>

    <main class="page-stack">
      <section v-if="selected" class="page-section">
        <h2>{{ selected.name }}：能力定义</h2>
        <div class="form-grid">
          <label class="field">
            <span>维度名称</span>
            <input v-model="form.name" />
          </label>
          <label class="field">
            <span>版本</span>
            <input v-model="form.version" />
          </label>
          <label class="field full">
            <span>维度定义</span>
            <textarea v-model="form.definition" />
          </label>
          <label class="field full">
            <span>可观察行为（一行一条）</span>
            <textarea v-model="form.observableText" />
          </label>
          <label class="field full">
            <span>无效证据说明</span>
            <textarea v-model="form.invalid_evidence_desc" />
          </label>
          <label class="field">
            <span>状态</span>
            <select v-model="form.status">
              <option value="active">active</option>
              <option value="draft">draft</option>
              <option value="disabled">disabled</option>
            </select>
          </label>
        </div>
        <div class="toolbar">
          <span class="muted">{{ message }}</span>
          <button class="primary-button" type="button" :disabled="saving" @click="saveDimension">
            保存能力定义
          </button>
        </div>
      </section>

      <section v-if="selected" class="page-section">
        <h2>评分锚点</h2>
        <table class="data-table">
          <thead>
            <tr>
              <th style="width: 72px">分值</th>
              <th style="width: 150px">水平名称</th>
              <th>行为描述</th>
              <th>典型证据</th>
              <th>反例/无效证据</th>
              <th style="width: 90px">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="anchor in anchorForms" :key="anchor.id">
              <td>{{ anchor.score_level }} 分</td>
              <td>
                <input v-model="anchor.level_name" />
              </td>
              <td>
                <textarea v-model="anchor.behavior_desc" />
              </td>
              <td>
                <textarea
                  :value="listToText(anchor.evidence_examples)"
                  @input="
                    anchor.evidence_examples = textToList(
                      ($event.target as HTMLTextAreaElement).value,
                    )
                  "
                />
              </td>
              <td>
                <textarea
                  :value="listToText(anchor.counter_examples)"
                  @input="
                    anchor.counter_examples = textToList(
                      ($event.target as HTMLTextAreaElement).value,
                    )
                  "
                />
              </td>
              <td>
                <button class="ghost-button" type="button" @click="saveAnchor(anchor)">
                  保存
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </main>
  </section>
</template>

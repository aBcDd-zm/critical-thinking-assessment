<script setup lang="ts">
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { createAssessmentSession } from "../api/session";
import TypewriterText from "../components/TypewriterText.vue";
import {
  OCCUPATION_CATEGORIES,
  type OccupationCategory,
} from "../types/session";

const router = useRouter();
const nickname = ref("");
const occupationCategory = ref<OccupationCategory | "">("");
const occupation = ref("");
const consentAccepted = ref(false);
const loading = ref(false);
const error = ref("");

const canStart = computed(
  () =>
    nickname.value.trim().length > 0 &&
    occupationCategory.value !== "" &&
    occupation.value.trim().length >= 2 &&
    consentAccepted.value &&
    !loading.value,
);
const occupationPlaceholder = computed(() => {
  if (occupationCategory.value === "学生") {
    return "例如：大学生、研究生、高中生";
  }
  if (occupationCategory.value === "待业/退休/其他") {
    return "例如：待业、退休、全职照护者";
  }
  return "例如：高中教师、产品经理、护士";
});
const introText = "你好，我是罗杰斯教授。开始前，请告诉我该怎么称呼你，以及你熟悉的职业或当前身份。";

async function startAssessment() {
  if (!canStart.value) return;
  loading.value = true;
  error.value = "";
  try {
    const session = await createAssessmentSession({
      nickname: nickname.value.trim(),
      occupation_category: occupationCategory.value as OccupationCategory,
      occupation: occupation.value.trim(),
      consent_accepted: true,
      consent_version: "critical_thinking_assessment_consent_v1",
    });
    localStorage.setItem("assessment_session_uuid", session.session_uuid);
    await router.push(`/assessment/session/${session.session_uuid}`);
  } catch (err) {
    error.value = "暂时无法创建测评会话，请确认后端服务和数据库已经启动。";
  } finally {
    loading.value = false;
  }
}

function resumeLastSession() {
  const sessionUuid = localStorage.getItem("assessment_session_uuid");
  if (sessionUuid) {
    router.push(`/assessment/session/${sessionUuid}`);
  }
}
</script>

<template>
  <main class="assessment-start immersive-start">
    <section class="opening-dialogue" aria-label="测评开始">
      <p class="speaker-label">罗杰斯教授</p>
      <h1>
        <TypewriterText :text="introText" :speed="24" />
      </h1>

      <form class="name-console minimal-name-console" @submit.prevent="startAssessment">
        <label class="assessment-field">
          <span class="assessment-field-label">怎么称呼你</span>
          <input
            v-model="nickname"
            autocomplete="nickname"
            maxlength="64"
            placeholder="请输入昵称"
          />
        </label>
        <label class="assessment-field">
          <span class="assessment-field-label">身份大类</span>
          <select v-model="occupationCategory" aria-label="职业大类">
            <option disabled value="">请选择最接近的一类</option>
            <option v-for="item in OCCUPATION_CATEGORIES" :key="item" :value="item">
              {{ item }}
            </option>
          </select>
        </label>
        <label class="assessment-field">
          <span class="assessment-field-label">你的具体身份</span>
          <input
            v-model="occupation"
            maxlength="64"
            :placeholder="occupationPlaceholder"
          />
        </label>
        <section class="assessment-consent" aria-labelledby="assessment-consent-title">
          <strong id="assessment-consent-title">测评与数据说明</strong>
          <p>
            罗杰斯教授是 AI 访谈角色；对话会由算法分析，并可能由授权专家复核。昵称与具体身份不会进入个人报告或匿名研究导出，去标识化数据可用于本项目研究。本测评不提供心理诊断或治疗，可随时结束。
          </p>
          <label class="consent-checkbox">
            <input v-model="consentAccepted" type="checkbox" />
            <span>我已阅读并同意以上说明</span>
          </label>
        </section>
        <p v-if="error" class="assessment-error">{{ error }}</p>
        <div class="start-actions">
          <button class="assessment-primary" type="submit" :disabled="!canStart">
            {{ loading ? "正在准备背景访谈" : "继续" }}
          </button>
          <button class="assessment-secondary" type="button" @click="resumeLastSession">
            继续上次
          </button>
        </div>
      </form>
    </section>
  </main>
</template>

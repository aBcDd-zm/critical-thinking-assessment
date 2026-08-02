<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { useAuthStore } from "../stores/auth";

const router = useRouter();
const auth = useAuthStore();

const username = ref("");
const password = ref("");
const loading = ref(false);
const error = ref("");

async function submit() {
  loading.value = true;
  error.value = "";
  try {
    await auth.login(username.value, password.value);
    router.push("/admin/dashboard");
  } catch {
    error.value = "账号或密码不正确，请检查后重试。";
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-box">
      <h1>测评配置台</h1>
      <p>维护能力模型、评分规则、情境阶段和追问策略。</p>

      <form class="page-stack" @submit.prevent="submit">
        <label class="field">
          <span>管理员账号</span>
          <input v-model="username" autocomplete="username" />
        </label>
        <label class="field">
          <span>管理员密码</span>
          <input v-model="password" type="password" autocomplete="current-password" />
        </label>
        <p v-if="error" class="error">{{ error }}</p>
        <button class="primary-button" type="submit" :disabled="loading">
          {{ loading ? "登录中..." : "登录后台" }}
        </button>
      </form>
    </section>
  </main>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";
import { useAuthStore } from "./stores/auth";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();

const isPublic = computed(() => Boolean(route.meta.public));
const pageMeta = computed(() => {
  const map: Record<string, { eyebrow: string; title: string }> = {
    dashboard: { eyebrow: "Assessment Analytics", title: "数据看板" },
    rubrics: { eyebrow: "Construct & Rubric", title: "能力与评分" },
    scenarios: { eyebrow: "Scenario Orchestration", title: "情境与追问" },
    "admin-sessions": { eyebrow: "Session Review", title: "会话复盘" },
    "admin-session-review": { eyebrow: "Session Evidence", title: "单次测评复盘" },
  };
  return map[String(route.name)] || { eyebrow: "Admin Console", title: "测评配置台" };
});

function logout() {
  auth.logout();
  router.push("/admin/login");
}
</script>

<template>
  <RouterView v-if="isPublic" />
  <div v-else class="admin-shell">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark">CT</span>
        <div>
          <strong>测评配置台</strong>
          <small>Critical Thinking</small>
        </div>
      </div>

      <nav class="nav">
        <RouterLink to="/admin/dashboard">
          <span>01</span>
          <strong>数据看板</strong>
        </RouterLink>
        <RouterLink to="/admin/rubrics">
          <span>02</span>
          <strong>能力与评分</strong>
        </RouterLink>
        <RouterLink to="/admin/scenarios">
          <span>03</span>
          <strong>情境与追问</strong>
        </RouterLink>
        <RouterLink to="/admin/sessions">
          <span>04</span>
          <strong>会话复盘</strong>
        </RouterLink>
      </nav>
    </aside>

    <main class="workspace">
      <header class="topbar">
        <div>
          <p class="eyebrow">{{ pageMeta.eyebrow }}</p>
          <h1>{{ pageMeta.title }}</h1>
        </div>
        <div class="account">
          <span class="status-dot"></span>
          <span>{{ auth.user?.display_name || auth.user?.username || "管理员" }}</span>
          <button class="ghost-button" type="button" @click="logout">退出</button>
        </div>
      </header>

      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{
  error?: string;
}>();

const emit = defineEmits<{
  retry: [];
}>();
</script>

<template>
  <section class="report-panel report-empty-state">
    <h2>报告尚未生成</h2>
    <p v-if="error" class="assessment-error">{{ error }}</p>
    <p v-else>
      报告正在生成中，请稍候。如果长时间未生成，请确认后端服务是否正常运行。
    </p>
    <button
      class="assessment-secondary"
      type="button"
      @click="emit('retry')"
    >
      重新加载
    </button>
  </section>
</template>

<script setup lang="ts">
const props = defineProps<{
  suggestions: string[];
  plan: string[];
}>();
</script>

<template>
  <section
    v-if="suggestions.length || plan.length"
    class="report-panel report-section improvement-plan"
  >
    <div v-if="suggestions.length" class="plan-block">
      <h2>改进建议</h2>
      <ul class="suggestion-list">
        <li
          v-for="(item, index) in suggestions"
          :key="`suggestion-${index}`"
          class="plan-item"
        >
          <span class="plan-marker">{{ index + 1 }}</span>
          <span class="plan-text">{{ item }}</span>
        </li>
      </ul>
    </div>

    <div v-if="plan.length" class="plan-block">
      <h3>发展计划</h3>
      <ul class="development-list">
        <li
          v-for="(item, index) in plan"
          :key="`plan-${index}`"
          class="plan-item"
        >
          <span class="plan-marker">{{ index + 1 }}</span>
          <span class="plan-text">{{ item }}</span>
        </li>
      </ul>
    </div>
  </section>
</template>

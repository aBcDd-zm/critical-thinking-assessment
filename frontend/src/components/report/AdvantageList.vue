<script setup lang="ts">
const props = defineProps<{
  items: string[];
}>();
</script>

<template>
  <section v-if="items.length" class="report-panel report-section advantage-list">
    <h2>优势分析</h2>
    <ul>
      <li v-for="(item, index) in items" :key="index" class="advantage-item">
        <span class="advantage-marker">+{{ index + 1 }}</span>
        <span class="advantage-text">{{ item }}</span>
      </li>
    </ul>
  </section>
</template>

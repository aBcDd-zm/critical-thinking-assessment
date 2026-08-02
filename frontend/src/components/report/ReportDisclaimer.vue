<script setup lang="ts">
const props = defineProps<{
  disclaimer: string;
}>();
</script>

<template>
  <footer class="report-panel report-section report-disclaimer-box">
    <strong>免责声明</strong>
    <p>{{ disclaimer }}</p>
  </footer>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";

const props = defineProps<{
  quote: string;
  evidenceType?: "supporting_evidence" | "weak_evidence" | "invalid_evidence" | string;
}>();

const typeLabelMap: Record<string, string> = {
  supporting_evidence: "有效证据",
  weak_evidence: "弱证据",
  invalid_evidence: "无效证据",
};

const label = props.evidenceType ? typeLabelMap[props.evidenceType] || "证据" : "证据";
const tagClass = props.evidenceType || "supporting_evidence";
const expanded = ref(false);
const canCollapse = computed(() => props.quote.trim().length > 160);
</script>

<template>
  <blockquote class="evidence-quote" :class="`evidence-${tagClass}`">
    <span class="evidence-tag">{{ label }}</span>
    <p :class="{ 'is-collapsed': canCollapse && !expanded }">「{{ quote }}」</p>
    <button
      v-if="canCollapse"
      class="evidence-toggle"
      type="button"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      {{ expanded ? "收起" : "展开原文" }}
    </button>
  </blockquote>
</template>

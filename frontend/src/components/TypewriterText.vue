<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";

const props = withDefaults(
  defineProps<{
    text: string;
    active?: boolean;
    delay?: number;
    speed?: number;
  }>(),
  {
    active: true,
    delay: 0,
    speed: 14,
  },
);

const visibleText = ref("");
const isTyping = ref(false);
let timerId: number | undefined;

const shouldAnimate = computed(() => {
  if (!props.active) return false;
  if (typeof window === "undefined") return false;
  return !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
});

function clearTimer() {
  if (timerId !== undefined) {
    window.clearTimeout(timerId);
    timerId = undefined;
  }
}

function startTyping() {
  clearTimer();
  const fullText = props.text || "";
  if (!shouldAnimate.value || fullText.length === 0) {
    visibleText.value = fullText;
    isTyping.value = false;
    return;
  }

  visibleText.value = "";
  isTyping.value = true;
  let index = 0;

  const tick = () => {
    visibleText.value = fullText.slice(0, index);
    if (index >= fullText.length) {
      isTyping.value = false;
      timerId = undefined;
      return;
    }
    index += 1;
    timerId = window.setTimeout(tick, props.speed);
  };

  timerId = window.setTimeout(tick, props.delay);
}

watch(
  () => [props.text, props.active, props.delay, props.speed],
  startTyping,
  { immediate: true },
);

onBeforeUnmount(clearTimer);
</script>

<template>
  <span class="typewriter-text">
    {{ visibleText }}<span v-if="isTyping" class="typewriter-caret"></span>
  </span>
</template>

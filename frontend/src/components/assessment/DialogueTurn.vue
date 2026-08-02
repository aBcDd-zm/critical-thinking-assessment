<script setup lang="ts">
import { computed } from "vue";
import type { DialogueTurnItem } from "../../types/session";

const props = defineProps<{
  turn: DialogueTurnItem;
  speakerLabel: string;
  streaming?: boolean;
  archiving?: boolean;
}>();

const isUser = computed(() => props.turn.speaker === "user");
const isAi = computed(() => props.turn.speaker === "ai");
const isDynamicInfo = computed(() =>
  ["dynamic_info", "dynamic_info_question"].includes(props.turn.content_type),
);

const turnTypeLabel = computed(() => {
  if (props.turn.speaker === "user") return "回答";
  if (props.turn.content_type === "intro_greeting") return "开场";
  if (props.turn.content_type === "intro_context") return "资料";
  if (props.turn.content_type === "stage_question") return "开场";
  if (["dynamic_info", "dynamic_info_question"].includes(props.turn.content_type)) return "新信息";
  if (props.turn.content_type === "clarification_response") return "题面说明";
  if (props.turn.content_type === "term_explanation") return "概念解释";
  if (props.turn.content_type === "redirect_response") return "回到本题";
  if (props.turn.content_type === "stage_incomplete_prompt") return "需要选择";
  if (props.turn.content_type === "stage_continue") return "继续补充";
  if (props.turn.content_type === "supplement_question") return "补充问题";
  if (props.turn.content_type === "guidance_response") return "作答提示";
  if (props.turn.content_type === "stage_skipped") return "已跳过";
  if (props.turn.content_type === "advance_prompt") return "过渡";
  if (props.turn.content_type === "followup_question") return "追问";
  return "对话";
});

const displayTime = computed(() => {
  const date = new Date(props.turn.created_at);
  if (Number.isNaN(date.getTime())) return "";
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
});
</script>

<template>
  <article
    class="interview-turn"
    :class="[
      isUser ? 'interview-turn-user' : 'interview-turn-ai',
      `interview-turn-${turn.content_type}`,
      props.archiving ? 'interview-turn-archiving' : '',
    ]"
  >
    <div v-if="isAi" class="interview-avatar" aria-hidden="true">
      <span></span>
    </div>

    <div class="interview-bubble">
      <header class="interview-bubble-meta">
        <strong>{{ speakerLabel }}</strong>
        <span>{{ turnTypeLabel }}</span>
        <time v-if="displayTime">{{ displayTime }}</time>
        <em v-if="isDynamicInfo">动态情境</em>
      </header>
      <p v-if="streaming" class="streaming-text">
        {{ turn.content }}<span class="typewriter-caret"></span>
      </p>
      <p v-else>{{ turn.content }}</p>
    </div>
  </article>
</template>

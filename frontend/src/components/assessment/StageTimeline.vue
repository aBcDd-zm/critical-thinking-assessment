<script setup lang="ts">
import type { StageProgressItem } from "../../types/session";

defineProps<{
  stages: StageProgressItem[];
}>();

function statusText(status: string) {
  if (status === "completed") return "已完成";
  if (status === "skipped") return "已跳过";
  if (status === "active") return "进行中";
  return "待开始";
}
</script>

<template>
  <ol class="stage-timeline" aria-label="测评阶段进度">
    <li
      v-for="stage in stages"
      :key="stage.stage_code"
      class="stage-timeline-item"
      :class="`stage-${stage.status}`"
    >
      <span class="stage-node" aria-hidden="true">{{ stage.stage_order }}</span>
      <div class="stage-copy">
        <div class="stage-title-row">
          <strong>{{ stage.title }}</strong>
          <span>{{ statusText(stage.status) }}</span>
        </div>
        <p>
          追问 {{ stage.used_followups }} / {{ stage.max_followups }}
          · 题面说明 {{ stage.used_clarifications }} / 2
          <template v-if="stage.released_dynamic_info_count > 0">
            · 新信息 {{ stage.released_dynamic_info_count }}
          </template>
        </p>
      </div>
    </li>
  </ol>
</template>

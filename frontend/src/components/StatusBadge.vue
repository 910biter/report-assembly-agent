<script setup lang="ts">
import { computed } from "vue";
import UiBadge from "@/components/ui/UiBadge.vue";
import { stageLabels } from "@/domain/workflowStages";
const props = defineProps<{ stage?: string }>();
const tone = computed(() =>
  props.stage === "failed"
    ? "danger"
    : ["done", "final"].includes(props.stage || "")
      ? "neutral"
      : props.stage === "review"
        ? "warning"
      : props.stage === "paused"
        ? "warning"
        : ["created", "draft"].includes(props.stage || "")
          ? "neutral"
          : "info",
);
</script>
<template>
  <UiBadge :tone="tone" :dot="true">{{
    stageLabels[stage || ""] || stage || "未知"
  }}</UiBadge>
</template>

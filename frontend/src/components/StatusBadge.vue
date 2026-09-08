<script setup lang="ts">
import { computed } from "vue";
import UiBadge from "@/components/ui/UiBadge.vue";
const props = defineProps<{ stage?: string }>();
const labels: Record<string, string> = {
  created: "待运行",
  parsing: "材料准备",
  dedup: "材料准备",
  material_analysis: "材料理解",
  requirement_review: "需求讨论",
  planning: "分析规划",
  evidence: "证据提取",
  conflict: "冲突核验",
  analysis: "综合分析",
  final_plan: "目录生成",
  directory_review: "目录讨论",
  narrative: "成文组织",
  writing: "报告生成",
  review: "待审核",
  done: "已完成",
  failed: "异常",
  paused: "已暂停",
  queued: "排队中",
  running: "运行中",
  draft: "草稿",
  final: "已定稿",
};
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
    labels[stage || ""] || stage || "未知"
  }}</UiBadge>
</template>
